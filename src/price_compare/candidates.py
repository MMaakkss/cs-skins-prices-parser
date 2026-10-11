"""Steam -> market.csgo.com resale candidates, checked against real sales.

The lowest ask on market.csgo.com says what a listing would be priced at, not
what it would sell for: a Steam -> TM shortlist built on asks alone turned out
to lose money once real sales were looked at. So the shortlist is built from
asks (cheap, already in the database) and then priced by the median of actual
sales from ``full-history`` (slow, one request per item).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from price_compare.feeds.market_csgo_history import MarketCsgoHistory
from price_compare.parsers.base import BaseParser

logger = logging.getLogger(__name__)

BUY_MARKET = "steam"
SELL_MARKET = "market_csgo"

# Latest Steam and market.csgo.com asks per item. LATERAL + LIMIT 1 walks
# ix_price_records_latest once per item; a DISTINCT ON over the whole table
# takes minutes on the server.
_PRICES = """
    SELECT i.id AS item_id, i.market_hash_name AS name,
           s.price AS steam, s.recorded_at AS steam_at,
           t.price AS tm_ask, t.volume AS tm_offers
    FROM items i
    {join} LATERAL (
        SELECT price, recorded_at FROM price_records
        WHERE item_id = i.id AND marketplace_id = :buy_id AND price_type = 'ask'
        ORDER BY recorded_at DESC LIMIT 1
    ) s ON true
    {join} LATERAL (
        SELECT price, volume, recorded_at FROM price_records
        WHERE item_id = i.id AND marketplace_id = :sell_id AND price_type = 'ask'
        ORDER BY recorded_at DESC LIMIT 1
    ) t ON true
"""

SHORTLIST_SQL = text(_PRICES.format(join="JOIN") + """
    WHERE s.price >= :price_min
      AND (CAST(:price_max AS numeric) IS NULL OR s.price <= :price_max)
      AND s.recorded_at > now() - make_interval(days => :steam_max_age_days)
      AND t.recorded_at > now() - make_interval(days => :tm_max_age_days)
      AND (t.price - 0.01) * (1 - :fee) / s.price - 1 BETWEEN :min_roi AND :max_roi
    ORDER BY (t.price - 0.01) * (1 - :fee) / s.price DESC
    LIMIT :limit
""")

NAMED_SQL = text(_PRICES.format(join="LEFT JOIN") + """
    WHERE i.market_hash_name = ANY(:names)
""")


@dataclass
class Candidate:
    item_id: int
    name: str
    steam: Decimal | None
    tm_ask: Decimal | None
    tm_offers: int | None
    stats: dict | None = None

    def profit(self, fee: Decimal) -> Decimal | None:
        """Selling at the 7-day median sale, after the marketplace's fee."""
        if self.steam is None or not self.stats or self.stats["median_7d"] is None:
            return None
        return self.stats["median_7d"] * (1 - fee) - self.steam


def marketplace(conn: Connection, name: str) -> tuple[int, Decimal]:
    row = conn.execute(
        text("SELECT id, sell_fee_percent FROM marketplaces WHERE name = :name"), {"name": name}
    ).one()
    return row.id, row.sell_fee_percent


def shortlist(
    conn: Connection,
    *,
    price_min: float,
    price_max: float | None,
    min_roi: float,
    max_roi: float,
    steam_max_age_days: int,
    tm_max_age_days: int,
    limit: int,
) -> list[Candidate]:
    buy_id, _ = marketplace(conn, BUY_MARKET)
    sell_id, fee = marketplace(conn, SELL_MARKET)
    rows = conn.execute(SHORTLIST_SQL, {
        "buy_id": buy_id, "sell_id": sell_id, "fee": fee,
        "price_min": price_min, "price_max": price_max,
        "min_roi": min_roi, "max_roi": max_roi,
        "steam_max_age_days": steam_max_age_days, "tm_max_age_days": tm_max_age_days,
        "limit": limit,
    }).all()
    return [Candidate(r.item_id, r.name, r.steam, r.tm_ask, r.tm_offers) for r in rows]


def named(conn: Connection, names: list[str]) -> tuple[list[Candidate], list[str]]:
    """The named items, and the names not found in the database."""
    buy_id, _ = marketplace(conn, BUY_MARKET)
    sell_id, _ = marketplace(conn, SELL_MARKET)
    names = [BaseParser._normalize_name(name) for name in names]
    rows = conn.execute(NAMED_SQL, {"buy_id": buy_id, "sell_id": sell_id, "names": names}).all()
    found = {r.name for r in rows}
    candidates = [Candidate(r.item_id, r.name, r.steam, r.tm_ask, r.tm_offers) for r in rows]
    return candidates, [name for name in names if name not in found]


def attach_stats(engine, candidates: list[Candidate], refresh_hours: float) -> None:
    """Give each candidate its sales stats, fetching only the stale ones.

    Each fetch is committed on its own, so an interrupted run keeps what it got
    and the next run skips it. Ctrl-C stops fetching; the candidates fetched so
    far are still returned with their stats.
    """
    from price_compare.db.models import SalesStats

    with engine.connect() as conn:
        sell_id, _ = marketplace(conn, SELL_MARKET)
        fresh_since = datetime.now(timezone.utc) - timedelta(hours=refresh_hours)
        for candidate in candidates:
            row = conn.execute(
                select(
                    SalesStats.as_of, SalesStats.sales_7d, SalesStats.sales_30d,
                    SalesStats.median_7d, SalesStats.median_30d, SalesStats.capped,
                    SalesStats.oldest_sale,
                )
                .where(SalesStats.item_id == candidate.item_id,
                       SalesStats.marketplace_id == sell_id,
                       SalesStats.as_of > fresh_since)
                .order_by(SalesStats.as_of.desc())
                .limit(1)
            ).mappings().first()
            if row:
                candidate.stats = dict(row)

    stale = [c for c in candidates if c.stats is None]
    if not stale:
        return
    logger.info(
        "full-history: %d of %d candidates need fetching (~16 s each on average)",
        len(stale), len(candidates),
    )

    history = MarketCsgoHistory()
    ids = history.load_index()
    try:
        for number, candidate in enumerate(stale, 1):
            tm_id = ids.get(candidate.name)
            if tm_id is None:
                logger.warning("full-history: %s is not in the index", candidate.name)
                continue
            started = datetime.now(timezone.utc)
            stats = history.fetch(tm_id)
            if stats is None:
                continue
            with engine.begin() as conn:
                conn.execute(
                    pg_insert(SalesStats)
                    .values(item_id=candidate.item_id, marketplace_id=sell_id, **stats)
                    .on_conflict_do_nothing()
                )
            candidate.stats = stats
            logger.info(
                "full-history %d/%d: %s — %d in 7d, %d in 30d (%.0fs)",
                number, len(stale), candidate.name, stats["sales_7d"], stats["sales_30d"],
                (datetime.now(timezone.utc) - started).total_seconds(),
            )
    except KeyboardInterrupt:
        logger.warning("full-history: interrupted, reporting what was fetched")
