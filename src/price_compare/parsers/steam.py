import logging
import re
import time

from price_compare.config import STEAM_PROXY_COOLDOWN, STEAM_REQUEST_DELAY
from price_compare.parsers.base import BaseParser

logger = logging.getLogger(__name__)

SEARCH_URL = "https://steamcommunity.com/market/search/render/"

EXTERIOR_TAGS = {
    "FN": "tag_WearCategory0",
    "MW": "tag_WearCategory1",
    "FT": "tag_WearCategory2",
    "WW": "tag_WearCategory3",
    "BS": "tag_WearCategory4",
}

CURRENCY_MAP = {
    "USD": 1,
    "GBP": 2,
    "EUR": 3,
}

COUNTRY_CODE_MAP = {
    "USD": "US",
    "GBP": "GB",
    "EUR": "DE",
}

# Steam now rejects "non-browser" requests to the market endpoints with 429.
# A full browser-like header set (Accept-*, Referer, X-Requested-With, the
# Sec-* / sec-ch-ua client hints) is required to get real data back.
STEAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://steamcommunity.com/market/search?appid=730",
    "X-Requested-With": "XMLHttpRequest",
    "sec-ch-ua": '"Chromium";v="126", "Not.A/Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class SteamParser(BaseParser):
    marketplace_name = "steam"
    base_url = "https://steamcommunity.com/market/"

    def __init__(self, currency: str = "USD", proxy_pool=None):
        super().__init__(STEAM_REQUEST_DELAY, proxy_pool)
        self.proxy_cooldown = STEAM_PROXY_COOLDOWN
        self.currency = currency
        self.session.headers.update(STEAM_HEADERS)

    def fetch_listings(self, filters: dict | None = None, count: int | None = None) -> list[dict]:
        filters = filters or {}
        results = []
        start = 0

        while True:
            if count and len(results) >= count:
                break

            params = self._build_params(filters, start=start, count=100)

            response = self._request(SEARCH_URL, params=params)
            if not response:
                break

            try:
                data = response.json()
            except ValueError:
                logger.error("Failed to parse JSON response from steam")
                break
            total_count = data.get("total_count", 0)

            for item in data.get("results", []):
                parsed = self._parse_listing(item)
                if parsed:
                    results.append(parsed)

            start += 100
            if start >= total_count:
                break

            time.sleep(self.request_delay)

        return results[:count] if count else results

    def _parse_listing(self, item: dict) -> dict | None:
        name = item.get("hash_name") or item.get("name")
        if not name:
            return None
        name = self._normalize_name(name)

        price_text = item.get("sell_price_text", "")
        price = self._parse_price(price_text)

        weapon, skin_name, exterior = self._parse_name(name)

        return {
            "market_hash_name": name,
            "price": price,
            "volume": item.get("sell_listings", 0),
            "currency": self.currency,
            "weapon": weapon,
            "skin_name": skin_name,
            "exterior": exterior,
            "stattrak": "StatTrak" in name,
            "souvenir": "Souvenir" in name,
        }

    @staticmethod
    def _parse_price(price_str: str) -> float:
        if not price_str:
            return 0.0
        cleaned = re.sub(r"[^\d.,]", "", price_str)
        cleaned = cleaned.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    
    def _build_params(self, filters: dict, start: int = 0, count: int = 100) -> dict:
        filters = filters or {}
        page_size = min(count, 100)

        params = {
            "appid": 730,
            "norender": 1,
            "count": page_size,
            "start": start,
            "sort_column": "popular",
            "sort_dir": "desc",
            "currency": CURRENCY_MAP.get(self.currency, 1),
            "cc": COUNTRY_CODE_MAP.get(self.currency, "US"),
        }
        if "exterior" in filters:
            tag = EXTERIOR_TAGS.get(filters["exterior"].upper())
            if tag:
                params["category_730_Exterior[]"] = tag

        if "weapon" in filters:
            params["category_730_Weapon[]"] = f"tag_weapon_{filters['weapon'].lower()}"

        if "quality" in filters:
            params["category_730_Quality[]"] = f"tag_{filters['quality']}"

        if "price_min" in filters:
            params["price_min"] = int(filters["price_min"] * 100)

        if "price_max" in filters:
            params["price_max"] = int(filters["price_max"] * 100)

        if "search" in filters:
            params["query"] = filters["search"]
            
        return params
