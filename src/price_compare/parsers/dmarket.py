import logging
import time
from urllib.parse import quote, urlencode, urlsplit

from price_compare.config import (
    DMARKET_PUBLIC_KEY,
    DMARKET_REQUEST_DELAY,
    DMARKET_SECRET_KEY,
)
from price_compare.parsers.base import BaseParser

logger = logging.getLogger(__name__)

API_ROOT = "https://api.dmarket.com"
SEARCH_PATH = "/marketplace-api/v2/offers"
SIGNATURE_PREFIX = "dmar ed25519 "

class DMarketParser(BaseParser):
    marketplace_name = "dmarket"
    base_url = "https://dmarket.com/"

    def __init__(self, currency: str = "USD", proxy_pool=None):
        super().__init__(DMARKET_REQUEST_DELAY, proxy_pool)
        # Note: DMarket returns prices in USD only
        self.currency = currency
        self._public_key = DMARKET_PUBLIC_KEY
        self._secret_key = DMARKET_SECRET_KEY

    def fetch_listings(self, filters: dict | None = None, count: int | None = None) -> list[dict]:
        if not self._public_key or not self._secret_key:
            logger.error(
                "DMarket requires Trading API keys. Set DMARKET_PUBLIC_KEY and "
                "DMARKET_SECRET_KEY in .env (dmarket.com -> Settings -> Trading API)."
            )
            return []

        filters = filters or {}
        seen = {}
        cursor = ""

        while True:
            if count and len(seen) >= count:
                break

            params = self._build_params(filters, cursor=cursor, count=100)

            url = f"{API_ROOT}{SEARCH_PATH}?{urlencode(params, quote_via=quote)}"
            response = self._request(url, params=None)
            if not response:
                break

            try:
                data = response.json()
            except ValueError:
                logger.error("Failed to parse JSON response from dmarket")
                break
            items = data.get("items") or []
            if not items:
                break

            for item in items:
                name = item.get("attributes", {}).get("title")
                if not name:
                    continue
                name = self._normalize_name(name)
                if name in seen:
                    continue

                # Offers are sorted by price asc, so the first offer seen for a
                # title is its cheapest. priceCents is a string of USD cents.
                price = int(item.get("priceCents", "0")) / 100.0
                weapon, skin_name, exterior = self._parse_name(name)

                seen[name] = {
                    "market_hash_name": name,
                    "weapon": weapon,
                    "skin_name": skin_name,
                    "exterior": exterior,
                    "price": price,
                    "volume": None,
                    "currency": "USD", # Note: DMarket returns prices in USD only
                    "stattrak": "StatTrak" in name,
                    "souvenir": "Souvenir" in name,
                }

            cursor = data.get("cursor", "")
            if not cursor:
                break

            time.sleep(self.request_delay)

        results = list(seen.values())
        return results[:count] if count else results

    def _build_params(self, filters: dict, cursor: str = "", count: int = 100) -> dict:
        filters = filters or {}
        params = {
            "gameId": "a8db",
            "currency": self.currency,
            "limit": min(count, 100),
            "orderBy": "price",
            "orderDir": "asc",
        }

        if cursor:
            params["cursor"] = cursor

        if "search" in filters:
            params["title"] = filters["search"]

        if "price_min" in filters:
            params["priceFrom"] = int(filters["price_min"] * 100)

        if "price_max" in filters:
            params["priceTo"] = int(filters["price_max"] * 100)

        return params

    def _auth_headers(self, method: str, url: str) -> dict:
        """Sign the request with the DMarket Ed25519 scheme.

        String to sign = METHOD + path(+?query) + body + timestamp. For GET the
        body is empty. Signature is the hex of the first 64 bytes of the NaCl
        signed message, prefixed with "dmar ed25519 ".
        """
        from nacl.bindings import crypto_sign

        parts = urlsplit(url)
        api_path = parts.path + (f"?{parts.query}" if parts.query else "")
        nonce = str(round(time.time()))
        string_to_sign = method.upper() + api_path + nonce
        signature = crypto_sign(
            string_to_sign.encode("utf-8"), bytes.fromhex(self._secret_key)
        )[:64].hex()

        return {
            "X-Api-Key": self._public_key,
            "X-Request-Sign": SIGNATURE_PREFIX + signature,
            "X-Sign-Date": nonce,
        }