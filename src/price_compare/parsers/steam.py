import json
import logging
import re
import time

from price_compare.config import STEAM_ACCEPT_ENCODING, STEAM_REQUEST_DELAY
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

# Prices are collected in USD only (Steam currency id 1, country US).
STEAM_CURRENCY_ID = 1
STEAM_COUNTRY_CODE = "US"

# Steam returns a relative icon path; prepend the CDN base for a usable URL.
STEAM_IMAGE_BASE = "https://community.cloudflare.steamstatic.com/economy/image/"

# The one header that decides whether a request is answered is Accept-Encoding:
# the rate-limit budget is keyed by that literal string and shared globally
# (docs/steam-rate-limits.md §1). The browser-like set below changes nothing on
# its own — it is kept only so the requests look like what the endpoint expects.
STEAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    # Set on the session, so it wins over the default `requests` would send.
    "Accept-Encoding": STEAM_ACCEPT_ENCODING,
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
    # A 429 here is a spent budget that refills slowly, and every probe can
    # refresh the block (§4), so the pass ends instead of knocking.
    retry_on_rate_limit = False

    def __init__(self):
        super().__init__(STEAM_REQUEST_DELAY)
        self.session.headers.update(STEAM_HEADERS)

    def fetch_listings(self, filters: dict | None = None, count: int | None = None) -> list[dict]:
        filters = filters or {}
        results = []

        # Only a full pass carries a resume point: a `--count N` run is an
        # interactive slice off the top and must not move the nightly pass's
        # offset.
        resumable = count is None
        start = self._load_resume_start(filters) if resumable else 0
        if start:
            logger.info("steam: resuming at start=%d", start)

        request_index = 0
        completed = False

        while True:
            if count and len(results) >= count:
                break

            params = self._build_params(filters, start=start)
            request_index += 1

            response = self._request(
                SEARCH_URL, params=params,
                context=f"request #{request_index}, start={start}",
            )
            if not response:
                logger.warning(
                    "steam: pass ended early at request #%d, start=%d (%d items collected)",
                    request_index, start, len(results),
                )
                break

            try:
                data = response.json()
            except ValueError:
                logger.error("Failed to parse JSON response from steam")
                break

            total_count = data.get("total_count", 0)
            page = data.get("results") or []
            logger.info(
                "steam request #%d: start=%d status=200 items=%d total_count=%d",
                request_index, start, len(page), total_count,
            )

            # An empty page is the only reliable end marker: total_count drifts
            # while a pass of thousands of requests is running.
            if not page:
                completed = True
                break

            for item in page:
                parsed = self._parse_listing(item)
                if parsed:
                    results.append(parsed)

            # Advance by what the response actually returned, never by a
            # constant — `count` is ignored and the page size is Valve's to
            # change (it dropped from 100 to 10).
            start += data.get("pagesize") or len(page)
            if start >= total_count:
                completed = True
                break

            time.sleep(self.request_delay)

        if resumable:
            self._save_resume_start(filters, 0 if completed else start)

        return results[:count] if count else results

    @staticmethod
    def _resume_key(filters: dict) -> str:
        return json.dumps(filters, sort_keys=True)

    def _load_resume_start(self, filters: dict) -> int:
        from price_compare.db.models import ParserState
        from price_compare.db.session import SessionLocal

        session = SessionLocal()
        try:
            state = session.query(ParserState).filter_by(
                marketplace=self.marketplace_name,
                filters_key=self._resume_key(filters),
            ).first()
            return state.next_start if state else 0
        finally:
            session.close()

    def _save_resume_start(self, filters: dict, next_start: int) -> None:
        """Record where the next pass over this filter set should begin.

        ``0`` means the pass finished and the next one starts from the top.
        """
        from price_compare.db.models import ParserState
        from price_compare.db.session import SessionLocal

        session = SessionLocal()
        try:
            state = session.query(ParserState).filter_by(
                marketplace=self.marketplace_name,
                filters_key=self._resume_key(filters),
            ).first()
            if state:
                state.next_start = next_start
            else:
                session.add(ParserState(
                    marketplace=self.marketplace_name,
                    filters_key=self._resume_key(filters),
                    next_start=next_start,
                ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _parse_listing(self, item: dict) -> dict | None:
        name = item.get("hash_name") or item.get("name")
        if not name:
            return None
        name = self._normalize_name(name)

        price_text = item.get("sell_price_text", "")
        price = self._parse_price(price_text)

        weapon, skin_name, exterior = self._parse_name(name)

        icon = item.get("asset_description", {}).get("icon_url")
        icon_url = f"{STEAM_IMAGE_BASE}{icon}" if icon else None

        return {
            "market_hash_name": name,
            "price": price,
            "volume": item.get("sell_listings", 0),
            "weapon": weapon,
            "skin_name": skin_name,
            "exterior": exterior,
            "icon_url": icon_url,
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
    
    def _build_params(self, filters: dict, start: int = 0) -> dict:
        filters = filters or {}

        # No `count`: search/render ignores it and returns its own page size.
        params = {
            "appid": 730,
            "norender": 1,
            "start": start,
            "sort_column": "popular",
            "sort_dir": "desc",
            "currency": STEAM_CURRENCY_ID,
            "cc": STEAM_COUNTRY_CODE,
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
