import logging
import re
import time

from price_compare.config import MARKET_CSGO_REQUEST_DELAY
from price_compare.parsers.base import BaseParser

logger = logging.getLogger(__name__)

API_ROOT = "https://market.csgo.com/api/v2"
# Lowest ask per item, whole market in one static file.
PRICES_URL = f"{API_ROOT}/prices/USD.json"
# Highest buy order per item, same shape.
ORDERS_URL = f"{API_ROOT}/prices/orders/USD.json"

# The CLI takes Steam's short wear codes; the names here are spelled out.
EXTERIORS = {
    "FN": "Factory New",
    "MW": "Minimal Wear",
    "FT": "Field-Tested",
    "WW": "Well-Worn",
    "BS": "Battle-Scarred",
}


class MarketCsgoParser(BaseParser):
    """market.csgo.com (TM Market).

    The marketplace publishes its whole CS2 price list as two static USD files,
    so a run is two requests: the lowest ask per item and the highest buy order
    per item. Both are stored — asks as ``ask`` records, buy orders as ``bid``.

    Nothing here is filterable server-side, so the CLI filters are applied to the
    downloaded dump. No API key is sent: the USD files are public, and the keyed
    endpoints price in the account's own currency, which breaks the USD-only rule.
    """

    marketplace_name = "market_csgo"
    base_url = "https://market.csgo.com/"

    def __init__(self):
        super().__init__(MARKET_CSGO_REQUEST_DELAY)
        # The price dumps are a few megabytes each.
        self.timeout = 60
        self._last_request = 0.0

    def fetch_listings(self, filters: dict | None = None, count: int | None = None) -> list[dict]:
        filters = filters or {}

        asks = self._fetch_prices(PRICES_URL, "ask")
        if not asks:
            return []

        asks = [listing for listing in asks if self._matches(listing, filters)]
        bids = self._fetch_prices(ORDERS_URL, "bid")

        # No server-side ordering exists and the file is alphabetical, so pick an
        # order for --count here. Open buy orders measure real demand; the ask
        # count does the opposite, since worthless skins are the ones that pile
        # up unsold. Items nobody bids on fall back to price, so the tail is
        # valuable-but-illiquid rather than sub-cent dust.
        bid_volume = {
            listing["market_hash_name"]: listing["volume"] or 0 for listing in bids
        }
        asks.sort(
            key=lambda listing: (
                bid_volume.get(listing["market_hash_name"], 0),
                listing["price"],
            ),
            reverse=True,
        )
        if count:
            asks = asks[:count]

        wanted = {listing["market_hash_name"] for listing in asks}
        bids = [listing for listing in bids if listing["market_hash_name"] in wanted]

        logger.info(
            "market_csgo: %d ask + %d bid listings", len(asks), len(bids),
        )
        return asks + bids

    def _fetch_prices(self, url: str, price_type: str) -> list[dict]:
        """Download one price dump and turn it into listing dicts."""
        response = self._request(url, params=None)
        if not response:
            return []

        try:
            data = response.json()
        except ValueError:
            logger.error("Failed to parse JSON response from market_csgo (%s)", url)
            return []

        if not data.get("success"):
            logger.error("market_csgo returned success=false for %s", url)
            return []
        currency = data.get("currency")
        if currency != "USD":
            # Prices are stored as USD without conversion, so anything else is
            # unusable rather than merely surprising.
            logger.error("market_csgo returned %s prices, expected USD", currency)
            return []

        listings = []
        for item in data.get("items") or []:
            listing = self._parse_listing(item, price_type)
            if listing:
                listings.append(listing)
        return listings

    def _parse_listing(self, item: dict, price_type: str) -> dict | None:
        name = item.get("market_hash_name")
        if not name:
            return None
        name = self._normalize_name(name)

        weapon, skin_name, exterior = self._parse_name(name)

        return {
            "market_hash_name": name,
            "price": self._to_float(item.get("price")),
            # Number of offers (asks) / open buy orders (bids) for the item.
            "volume": self._to_int(item.get("volume")),
            "weapon": weapon,
            "skin_name": skin_name,
            "exterior": exterior,
            # The price dumps carry no images; other parsers backfill icon_url.
            "icon_url": None,
            "stattrak": "StatTrak" in name,
            "souvenir": "Souvenir" in name,
            "price_type": price_type,
        }

    def _matches(self, listing: dict, filters: dict) -> bool:
        """Apply the CLI filters locally — the dump has no query parameters."""
        if "search" in filters:
            if filters["search"].lower() not in listing["market_hash_name"].lower():
                return False

        if "price_min" in filters and listing["price"] < filters["price_min"]:
            return False

        if "price_max" in filters and listing["price"] > filters["price_max"]:
            return False

        if "exterior" in filters:
            wanted = EXTERIORS.get(filters["exterior"].upper())
            if wanted and listing["exterior"] != wanted:
                return False

        if "weapon" in filters:
            # The CLI speaks Steam's tag form ("ak47"), the names here are
            # "AK-47" — compare with punctuation and case stripped out.
            if self._slug(listing["weapon"]) != self._slug(filters["weapon"]):
                return False

        return True

    def _request(self, url: str, params: dict, max_retries: int | None = None, context: str = ""):
        """Space requests out — market.csgo.com deletes keys that exceed 5 req/s.

        The guard lives here rather than around the call sites so retries inside
        ``BaseParser._request`` cannot burst past the limit either.
        """
        wait = self._last_request + self.request_delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        return super()._request(url, params, max_retries, context)

    @staticmethod
    def _slug(value: str | None) -> str:
        return re.sub(r"[^a-z0-9]", "", (value or "").lower())

    @staticmethod
    def _to_float(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _to_int(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
