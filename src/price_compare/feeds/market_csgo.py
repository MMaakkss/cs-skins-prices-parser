"""Live sales feed of market.csgo.com, stored one row per sale.

The site's own frontend subscribes to a Centrifugo channel that publishes every
CS2 sale on the marketplace. It is undocumented — found in the site's JS bundle
— and needs no API key: an anonymous token from ``/api/centrifugo/refresh`` is
enough. Measured on 2026-10-05:

- complete: all 174 sales that ``get-list-items-info`` reported for 12 items
  over a 10-minute window were in the feed, same second, same price;
- ~2.2-2.6 messages/s, 1.4 KB/s, 25 MB RSS for the bare client;
- some sales arrive twice, and ~7 days after a sale it is sent again; the
  messages are byte-identical, so the table's primary key collapses them;
- ~2% of messages are empty (no name, zero price) and are skipped.

The process runs until stopped and reconnects on any failure. Rows are written
in small batches; a crash loses at most one batch, and the week-later re-send
fills most of that back in.
"""
import json
import logging
import signal
import time as clock
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from price_compare.config import SALES_RETENTION_DAYS
from price_compare.feeds import partitions
from price_compare.parsers.base import USER_AGENT, BaseParser

logger = logging.getLogger(__name__)

SITE = "https://market.csgo.com"
TOKEN_URL = f"{SITE}/api/centrifugo/refresh"
WS_URL = "wss://market.csgo.com/connection/websocket"
CHANNEL = "public:history:730"
HEADERS = {"User-Agent": USER_AGENT, "Origin": SITE}

FLUSH_SECONDS = 5
FLUSH_ROWS = 500
# Rows held while the database is unreachable; past this the oldest go.
MAX_BUFFER = 50_000
STATS_SECONDS = 600
# Refresh the connection token this long before it expires.
REFRESH_MARGIN = 60
REFRESH_RETRY = 10
MAX_BACKOFF = 60


class FeedError(Exception):
    """The server refused a command; the connection is restarted."""


class MarketCsgoSalesFeed:
    marketplace_name = "market_csgo"
    base_url = "https://market.csgo.com/"

    def __init__(self, retention_days: int = SALES_RETENTION_DAYS):
        self.retention_days = retention_days
        self._stop = False
        self._buffer: list[tuple[str, datetime, Decimal]] = []
        self._item_ids: dict[str, int] = {}
        self._marketplace_id: int | None = None
        self._maintained_on = None
        self._command_id = 0
        self._stats = dict.fromkeys(("received", "empty", "outside", "stored", "duplicate"), 0)
        self._stats_at = clock.monotonic()

    def run(self) -> None:
        """Listen until SIGTERM / SIGINT, reconnecting on any failure."""
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

        self._prepare()
        backoff = 1
        while not self._stop:
            started = clock.monotonic()
            try:
                self._listen()
            except (WebSocketException, OSError, TimeoutError, FeedError,
                    requests.RequestException) as e:
                logger.warning("sales feed: connection lost: %s: %s", type(e).__name__, e)
            except Exception:
                # Anything else is a bug or a changed message format. Keep
                # listening — a stopped feed loses sales for good — but leave
                # the traceback in the log.
                logger.exception("sales feed: unexpected error")
            self._flush()
            if self._stop:
                break
            # A connection that held for a while was a normal drop, not a
            # failing server: start the backoff over.
            if clock.monotonic() - started > 60:
                backoff = 1
            logger.info("sales feed: reconnecting in %ds", backoff)
            self._sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
        self._flush()
        logger.info("sales feed: stopped")

    def _request_stop(self, signum, frame) -> None:
        logger.info("sales feed: signal %d, stopping after the current batch", signum)
        self._stop = True

    def _sleep(self, seconds: float) -> None:
        end = clock.monotonic() + seconds
        while not self._stop and clock.monotonic() < end:
            clock.sleep(min(1, end - clock.monotonic()))

    # -- connection -------------------------------------------------------

    def _listen(self) -> None:
        with connect(WS_URL, additional_headers=HEADERS, open_timeout=15, max_size=2**22) as ws:
            reply = self._call(ws, {"connect": {"name": "js", "token": self._token()}})
            ttl = reply["connect"].get("ttl")
            ping = reply["connect"].get("ping", 25)
            self._call(ws, {"subscribe": {"channel": CHANNEL}})
            logger.info("sales feed: subscribed to %s (token ttl %ss)", CHANNEL, ttl)

            refresh_at = clock.monotonic() + ttl - REFRESH_MARGIN if ttl else None
            last_frame = clock.monotonic()
            last_flush = clock.monotonic()
            while not self._stop:
                now = clock.monotonic()
                if refresh_at and now >= refresh_at:
                    # The token endpoint sometimes takes longer than the timeout.
                    # The current token is still good for REFRESH_MARGIN seconds,
                    # so retry instead of dropping a working connection.
                    try:
                        token = self._token()
                    except requests.RequestException as e:
                        logger.warning(
                            "sales feed: token refresh failed (%s), retrying in %ds",
                            type(e).__name__, REFRESH_RETRY,
                        )
                        refresh_at = now + REFRESH_RETRY
                    else:
                        self._command_id += 1
                        ws.send(json.dumps({"id": self._command_id, "refresh": {"token": token}}))
                        refresh_at = now + ttl - REFRESH_MARGIN
                if now - last_flush >= FLUSH_SECONDS or len(self._buffer) >= FLUSH_ROWS:
                    self._flush()
                    last_flush = now
                if now - self._stats_at >= STATS_SECONDS:
                    self._log_stats()
                # The server pings every `ping` seconds; silence for several of
                # them is a dead connection the socket has not noticed yet.
                if now - last_frame > ping * 3:
                    raise TimeoutError(f"no frames for {now - last_frame:.0f}s")

                try:
                    frame = ws.recv(timeout=1)
                except TimeoutError:
                    continue
                last_frame = clock.monotonic()
                for line in frame.splitlines():
                    self._handle(ws, line)

    def _call(self, ws, command: dict) -> dict:
        """Send a command and wait for its reply, buffering pushes meanwhile."""
        self._command_id += 1
        command_id = self._command_id
        ws.send(json.dumps({"id": command_id, **command}))
        deadline = clock.monotonic() + 15
        while clock.monotonic() < deadline:
            for line in ws.recv(timeout=deadline - clock.monotonic()).splitlines():
                message = self._handle(ws, line)
                if message and message.get("id") == command_id:
                    return message
        raise TimeoutError(f"no reply to {next(iter(command))}")

    def _handle(self, ws, line: str) -> dict | None:
        if not line:
            return None
        if line == "{}":
            # Centrifugo's ping; it expects the same back.
            ws.send("{}")
            return None
        message = json.loads(line)
        if "error" in message:
            raise FeedError(message["error"])
        push = message.get("push")
        if push:
            if "disconnect" in push:
                raise FeedError(f"server disconnect: {push['disconnect']}")
            pub = push.get("pub")
            if pub and push.get("channel") == CHANNEL:
                self._accept(pub.get("data") or {})
        return message

    def _token(self) -> str:
        response = requests.get(TOKEN_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        return response.json()["token"]

    # -- messages ---------------------------------------------------------

    def _accept(self, data: dict) -> None:
        self._stats["received"] += 1
        payload = data.get("payload") or {}
        name = payload.get("market_hash_name")
        sale_time = payload.get("sale_time")
        price = next(
            (p.get("price") for p in payload.get("prices") or [] if p.get("currency") == "USD"),
            None,
        )
        if data.get("type") != "history" or not name or not sale_time or not price or price <= 0:
            self._stats["empty"] += 1
            return
        self._buffer.append((
            BaseParser._normalize_name(name),
            datetime.fromtimestamp(int(sale_time), tz=timezone.utc),
            Decimal(str(price)),
        ))

    # -- database ---------------------------------------------------------

    def _prepare(self) -> None:
        """Marketplace row, the name -> id cache and today's partitions."""
        from price_compare.db.models import Item, Marketplace
        from price_compare.db.session import engine

        with engine.begin() as conn:
            self._marketplace_id = conn.execute(
                select(Marketplace.id).where(Marketplace.name == self.marketplace_name)
            ).scalar()
            if self._marketplace_id is None:
                self._marketplace_id = conn.execute(
                    pg_insert(Marketplace)
                    .values(name=self.marketplace_name, url=self.base_url)
                    .returning(Marketplace.id)
                ).scalar_one()
            self._item_ids = dict(conn.execute(select(Item.market_hash_name, Item.id)).all())
        logger.info("sales feed: %d known items", len(self._item_ids))
        self._maintain()

    def _maintain(self) -> None:
        from price_compare.db.session import engine

        today = datetime.now(timezone.utc).date()
        if self._maintained_on == today:
            return
        with engine.begin() as conn:
            partitions.maintain(conn, today, self.retention_days)
        self._maintained_on = today

    def _flush(self) -> None:
        if not self._buffer:
            return
        rows, self._buffer = self._buffer, []
        try:
            self._maintain()
            self._write(rows)
        except Exception:
            logger.exception("sales feed: failed to write %d sales, keeping them", len(rows))
            self._buffer = (rows + self._buffer)[-MAX_BUFFER:]

    def _write(self, rows: list[tuple[str, datetime, Decimal]]) -> None:
        from price_compare.db.models import Sale
        from price_compare.db.session import engine

        today = datetime.now(timezone.utc).date()
        low = partitions.day_start(partitions.oldest_kept_day(today, self.retention_days))
        high = partitions.day_start(today) + timedelta(days=partitions.DAYS_AHEAD + 1)
        inside = [row for row in rows if low <= row[1] < high]
        self._stats["outside"] += len(rows) - len(inside)
        if not inside:
            return

        with engine.begin() as conn:
            self._ensure_items(conn, {name for name, _, _ in inside})
            stored = conn.execute(
                pg_insert(Sale)
                .values([
                    {
                        "item_id": self._item_ids[name],
                        "marketplace_id": self._marketplace_id,
                        "sold_at": sold_at,
                        "price": price,
                    }
                    for name, sold_at, price in inside
                ])
                .on_conflict_do_nothing()
                .returning(Sale.item_id)
            ).all()
        self._stats["stored"] += len(stored)
        self._stats["duplicate"] += len(inside) - len(stored)

    def _ensure_items(self, conn, names: set[str]) -> None:
        from price_compare.db.models import Item

        missing = names - self._item_ids.keys()
        if not missing:
            return
        rows = []
        for name in missing:
            weapon, skin_name, exterior = BaseParser._parse_name(name)
            rows.append({
                "market_hash_name": name,
                "weapon": weapon,
                "skin_name": skin_name,
                "exterior": exterior,
                "stattrak": "StatTrak" in name,
                "souvenir": "Souvenir" in name,
            })
        conn.execute(
            pg_insert(Item).values(rows).on_conflict_do_nothing(index_elements=["market_hash_name"])
        )
        self._item_ids.update(conn.execute(
            select(Item.market_hash_name, Item.id).where(Item.market_hash_name.in_(missing))
        ).all())
        logger.info("sales feed: added %d new items", len(missing))

    def _log_stats(self) -> None:
        s = self._stats
        logger.info(
            "sales feed: %d received, %d stored, %d duplicate, %d empty, %d outside retention",
            s["received"], s["stored"], s["duplicate"], s["empty"], s["outside"],
        )
        self._stats = dict.fromkeys(s, 0)
        self._stats_at = clock.monotonic()
