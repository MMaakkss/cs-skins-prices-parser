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

    def __init__(self):
        super().__init__(DMARKET_REQUEST_DELAY)
        self._public_key = DMARKET_PUBLIC_KEY
        self._secret_key = DMARKET_SECRET_KEY

    def fetch_listings(self, filters: dict | None = None, count: int | None = None) -> list[dict]:
        """Collect a whole pass into one list.

        `run()` goes through `iter_listings()` instead, which writes each page as
        it arrives; this stays for callers that want the listings themselves.
        """
        return [item for page, _ in self.iter_listings(filters=filters, count=count) for item in page]

    def iter_listings(self, filters: dict | None = None, count: int | None = None):
        """Yield each page's new titles, so a long pass is persisted as it runs.

        A full pass walks every *offer*, not every item, and collapses them to
        the cheapest offer per title — so the deeper it goes the more requests it
        spends per new name, and the whole thing runs for hours. Writing only at
        the end would hand all of that to the first crash.

        The resume point is a price in cents. Offers arrive in ascending price
        order, so the next run can start from the last price seen instead of
        paging through what it already has. The cursor would be the natural
        bookmark, but it is an opaque string where `parser_state` holds an
        integer — and a price survives DMarket rotating its cursors, which an
        opaque token would not.
        """
        if not self._public_key or not self._secret_key:
            logger.error(
                "DMarket requires Trading API keys. Set DMARKET_PUBLIC_KEY and "
                "DMARKET_SECRET_KEY in .env (dmarket.com -> Settings -> Trading API)."
            )
            return

        filters = filters or {}

        # Only a full pass carries a resume point; `--count N` is a slice off the
        # cheap end and must not move it.
        resumable = count is None
        resume_cents = self._load_resume_start(filters) if resumable else 0
        if resume_cents:
            logger.info("dmarket: resuming from $%.2f", resume_cents / 100)

        seen: set[str] = set()
        collected = 0
        cursor = ""
        last_cents = resume_cents
        request_index = 0

        while True:
            if count and collected >= count:
                break

            params = self._build_params(filters, cursor=cursor, count=100, min_cents=resume_cents)
            url = f"{API_ROOT}{SEARCH_PATH}?{urlencode(params, quote_via=quote)}"
            request_index += 1

            response = self._request(url, params=None, context=f"request #{request_index}")
            if not response:
                # Nothing is yielded, so the resume point stays at the last page
                # that was committed — which is where to continue.
                logger.warning(
                    "dmarket: pass ended early at request #%d (%d items collected)",
                    request_index, collected,
                )
                break

            try:
                data = response.json()
            except ValueError:
                logger.error("Failed to parse JSON response from dmarket")
                break

            items = data.get("items") or []
            if not items:
                if resumable:
                    yield [], 0
                break

            page = []
            for item in items:
                name = item.get("attributes", {}).get("title")
                if not name:
                    continue
                name = self._normalize_name(name)

                # priceCents is a string of USD cents.
                cents = int(item.get("priceCents", "0"))
                last_cents = max(last_cents, cents)

                # Offers are sorted by price asc, so the first offer seen for a
                # title is its cheapest; later ones are the same item dearer.
                if name in seen:
                    continue
                seen.add(name)

                weapon, skin_name, exterior = self._parse_name(name)
                page.append({
                    "market_hash_name": name,
                    "weapon": weapon,
                    "skin_name": skin_name,
                    "exterior": exterior,
                    "price": cents / 100.0,
                    "volume": None,
                    "icon_url": item.get("image"),
                    "stattrak": "StatTrak" in name,
                    "souvenir": "Souvenir" in name,
                })

            if count:
                page = page[: count - collected]
            collected += len(page)

            cursor = data.get("cursor", "")
            finished = not cursor
            if request_index % 20 == 0:
                logger.info(
                    "dmarket request #%d: %d items collected, at $%.2f",
                    request_index, collected, last_cents / 100,
                )

            yield page, (0 if finished else last_cents) if resumable else None

            if finished:
                break

            time.sleep(self.request_delay)

    def _build_params(
        self, filters: dict, cursor: str = "", count: int = 100, min_cents: int = 0
    ) -> dict:
        filters = filters or {}
        params = {
            "gameId": "a8db",
            "currency": "USD",  # DMarket returns prices in USD only
            "limit": min(count, 100),
            "orderBy": "price",
            "orderDir": "asc",
        }

        if cursor:
            params["cursor"] = cursor

        if "search" in filters:
            params["title"] = filters["search"]

        # The caller's floor and the resume point are the same knob; the
        # higher of the two wins, so resuming never widens a filtered pass.
        floor = max(int(filters.get("price_min", 0) * 100), min_cents)
        if floor:
            params["priceFrom"] = floor

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