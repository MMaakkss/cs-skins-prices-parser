"""Exact sales volume per item from market.csgo.com's ``full-history`` files.

Two public files, no API key:

- ``full-history/all.json`` maps every market_hash_name to a numeric id;
- ``full-history/{id}.json`` holds the item's last (up to) 6,000 sales as
  ``[unix time, RUB, USD, EUR]`` plus the marketplace's own 7/30-day counts.

The counts match ``get-list-items-info`` exactly and, unlike the live feed,
include identical sales in the same second. The cost is speed: measured on
2026-10-06 over 40 random ids, half came back from cache in under a second, the
rest took ~5-40 s and a third failed with Cloudflare's 522 — about 16 s per item
on average. Fine for a few dozen candidates, days for the whole market, so
requests stay sequential: the endpoint's limits are unknown and it is keyless,
so overload would be charged to the server's IP.
"""
import logging
import statistics
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import requests

from price_compare.parsers.base import USER_AGENT, BaseParser

logger = logging.getLogger(__name__)

API_ROOT = "https://market.csgo.com/api/v2/full-history"
INDEX_URL = f"{API_ROOT}/all.json"
HISTORY_CAP = 6000
# A cold file takes up to ~40 s before Cloudflare gives up with a 522; the
# server usually finishes building it anyway, so the retry is the fast one.
TIMEOUT = 60
ATTEMPTS = 3
RETRY_PAUSE = 5
REQUEST_DELAY = 0.5


class MarketCsgoHistory:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request = 0.0

    def load_index(self) -> dict[str, int]:
        """market_hash_name (normalized) -> full-history id."""
        data = self._get(INDEX_URL)
        if not data or "history" not in data:
            raise RuntimeError("market_csgo full-history index unavailable")
        return {BaseParser._normalize_name(name): tm_id for name, tm_id in data["history"].items()}

    def fetch(self, tm_id: int) -> dict | None:
        """The item's stats, or None if the file could not be fetched.

        Returns ``as_of``, ``sales_7d``, ``sales_30d``, ``median_7d``,
        ``median_30d``, ``capped`` and ``oldest_sale``, medians in USD.
        """
        data = self._get(f"{API_ROOT}/{tm_id}.json")
        if not data or "data" not in data:
            return None
        return self.summarize(data)

    @staticmethod
    def summarize(data: dict) -> dict:
        body = data["data"]
        as_of = datetime.fromtimestamp(int(data["time"]), tz=timezone.utc)
        history = body.get("history") or []

        def window(days: int) -> list[float]:
            since = (as_of - timedelta(days=days)).timestamp()
            return [sale[2] for sale in history if sale[0] > since]

        def median(prices: list[float]) -> Decimal | None:
            return Decimal(str(round(statistics.median(prices), 4))) if prices else None

        week, month = window(7), window(30)
        return {
            "as_of": as_of,
            # The marketplace's own counts, which agree with counting `history`
            # over the same windows.
            "sales_7d": int(body["sales7d"]["USD"]),
            "sales_30d": int(body["sales30d"]["USD"]),
            "median_7d": median(week),
            "median_30d": median(month),
            "capped": len(history) >= HISTORY_CAP,
            "oldest_sale": (
                datetime.fromtimestamp(min(sale[0] for sale in history), tz=timezone.utc)
                if history else None
            ),
        }

    def _get(self, url: str) -> dict | None:
        for attempt in range(1, ATTEMPTS + 1):
            wait = self._last_request + REQUEST_DELAY - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                response = self.session.get(url, timeout=TIMEOUT)
                if response.status_code == 200:
                    return response.json()
                logger.debug("HTTP %s for %s (attempt %d)", response.status_code, url, attempt)
            except (requests.RequestException, ValueError) as e:
                logger.debug("%s for %s (attempt %d)", type(e).__name__, url, attempt)
            if attempt < ATTEMPTS:
                time.sleep(RETRY_PAUSE)
        logger.warning("market_csgo full-history: gave up on %s after %d attempts", url, ATTEMPTS)
        return None
