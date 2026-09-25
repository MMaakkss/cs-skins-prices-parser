import logging
import re
import time
import unicodedata
from abc import ABC, abstractmethod
from decimal import Decimal

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class BaseParser(ABC):
    marketplace_name: str
    base_url: str | None = None
    # Whether a 429 is worth waiting out. False for marketplaces whose block a
    # retry only prolongs — those get None back and stop the pass instead.
    retry_on_rate_limit: bool = True

    def __init__(self, request_delay: float):
        self.request_delay = request_delay
        # Per-request timeout. Subclasses pulling multi-megabyte dumps raise it.
        self.timeout = 15
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    @abstractmethod
    def fetch_listings(self, filters: dict | None = None, count: int | None = None) -> list[dict]:
        """Fetch item listings from the marketplace."""

    def _auth_headers(self, method: str, url: str) -> dict | None:
        """Per-request auth headers (e.g. request signing).

        Default: no auth. Subclasses that need signed requests (DMarket)
        override this. Called fresh on every attempt so timestamps stay valid.
        """
        return None

    def _request(
        self,
        url: str,
        params: dict,
        max_retries: int | None = None,
        context: str = "",
    ) -> requests.Response | None:
        """GET a URL, retrying transient failures.

        A 429 is backed off and retried, unless the parser sets
        ``retry_on_rate_limit = False``: there the request returns None right
        away so the caller can end the pass and keep what it already collected.
        ``context`` is appended to the log lines — pass whatever identifies the
        request within the pass (index, page offset).
        """
        if max_retries is None:
            max_retries = 3
        where = f" ({context})" if context else ""

        for attempt in range(max_retries):
            headers = self._auth_headers("GET", url)
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout, headers=headers)
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 429:
                    if not self.retry_on_rate_limit:
                        logger.warning(
                            "Rate limited (429) by %s%s; ending the pass",
                            self.marketplace_name, where,
                        )
                        return None
                    wait = self.request_delay * (attempt + 2)
                    logger.warning(
                        "Rate limited by %s%s, waiting %ss...",
                        self.marketplace_name, where, wait,
                    )
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    time.sleep(self.request_delay)
                    continue
                logger.warning("HTTP %s for %s%s", resp.status_code, url, where)
                return None
            except requests.RequestException as e:
                logger.warning("Request error%s: %s", where, e)
                time.sleep(self.request_delay)
        return None

    def run(self, filters: dict | None = None, count: int | None = None) -> list[dict]:
        """Fetch listings and append a fresh price snapshot for each to the DB.

        Every run inserts a new PriceRecord per item so price history
        accumulates over time; existing records are never overwritten.
        Listings without a valid (positive) price are skipped and logged.
        """
        from price_compare.db.models import Item, Marketplace, PriceRecord
        from price_compare.db.session import SessionLocal

        listings = self.fetch_listings(filters=filters, count=count)

        session = SessionLocal()
        saved = []
        skipped = 0
        try:
            marketplace = session.query(Marketplace).filter_by(name=self.marketplace_name).first()
            if not marketplace:
                marketplace = Marketplace(name=self.marketplace_name, url=self.base_url)
                session.add(marketplace)
                session.flush()

            for listing in listings:
                price = listing.get("price")
                if not price or price <= 0:
                    skipped += 1
                    logger.warning(
                        "Skipping %r: invalid price %r",
                        listing.get("market_hash_name"),
                        price,
                    )
                    continue

                item = session.query(Item).filter_by(
                    market_hash_name=listing["market_hash_name"]
                ).first()
                if not item:
                    item = Item(
                        market_hash_name=listing["market_hash_name"],
                        weapon=listing.get("weapon"),
                        skin_name=listing.get("skin_name"),
                        exterior=listing.get("exterior"),
                        icon_url=listing.get("icon_url"),
                        stattrak=listing.get("stattrak", False),
                        souvenir=listing.get("souvenir", False),
                    )
                    session.add(item)
                    session.flush()
                elif not item.icon_url and listing.get("icon_url"):
                    # Backfill the preview once a marketplace provides one.
                    item.icon_url = listing["icon_url"]

                session.add(PriceRecord(
                    item_id=item.id,
                    marketplace_id=marketplace.id,
                    price=Decimal(str(price)),
                    volume=listing.get("volume"),
                    # Most parsers only report the lowest ask; a marketplace that
                    # also exposes buy orders sets price_type="bid" on those.
                    price_type=listing.get("price_type", "ask"),
                ))
                saved.append(listing)

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        logger.info(
            "%s: saved %d price records, skipped %d",
            self.marketplace_name, len(saved), skipped,
        )
        return saved

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize a market hash name without altering its visible content.

        Fixes only invisible inconsistencies so the same skin coming from
        different marketplaces maps to one record:
          - NFC unicode form (canonical; keeps ™ as a single char, unlike NFKC
            which would expand ™ into "TM");
          - non-breaking / narrow spaces -> regular space;
          - collapsed whitespace runs and trimmed ends.
        The name itself (™, wording, punctuation) is preserved as-is.
        """
        name = unicodedata.normalize("NFC", name)
        name = re.sub(r"\s+", " ", name)
        return name.strip()

    @staticmethod
    def _parse_name(name: str) -> tuple[str | None, str | None, str | None]:
        exterior_match = re.search(r"\(([^)]+)\)$", name)
        exterior = exterior_match.group(1) if exterior_match else None

        base = name
        if exterior_match:
            base = name[: exterior_match.start()].strip()

        base = re.sub(r"^(StatTrak™?|Souvenir)\s+", "", base)

        parts = base.split(" | ", 1)
        weapon = parts[0].strip() if len(parts) == 2 else None
        skin_name = parts[1].strip() if len(parts) == 2 else base.strip()

        return weapon, skin_name, exterior