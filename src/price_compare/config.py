import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


# The `+psycopg` driver is spelled out on purpose: a bare `postgresql://` URL
# resolves to whatever DBAPI the installed SQLAlchemy defaults to, and that
# default changed in 2.1.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/price_compare",
)

# Steam's market limit is not per IP: the budget is keyed by the exact
# Accept-Encoding string and shared globally (docs/steam-rate-limits.md).
# The budget refills at ~0.223 req/s — one request per 4.48s — measured over a
# three-hour pass (§8). 4.0s looked conservative and was in fact a 4% deficit
# that killed the pass at 70% of the market; 4.5s leaves ~10% of headroom.
STEAM_REQUEST_DELAY = float(os.getenv("STEAM_REQUEST_DELAY", "4.5"))
# The identity of this project's own rate-limit bucket. It must stay literally
# different from the client defaults (`requests` sends `gzip, deflate, br`,
# which every other scraper using the library is already emptying) and it must
# stay *stable*: one private bucket, not a rotating set of them. Do not reuse
# this value in the other parsers — they are limited per API key.
STEAM_ACCEPT_ENCODING = os.getenv("STEAM_ACCEPT_ENCODING", "gzip;q=0.97, deflate")
DMARKET_REQUEST_DELAY = float(os.getenv("DMARKET_REQUEST_DELAY", "1.0"))
# market.csgo.com deletes an API key that sends more than 5 requests per second;
# 0.25s between requests (4 req/s) keeps a margin.
MARKET_CSGO_REQUEST_DELAY = float(os.getenv("MARKET_CSGO_REQUEST_DELAY", "0.25"))

# DMarket Trading API keys (required — DMarket's market API needs signed requests).
DMARKET_PUBLIC_KEY = os.getenv("DMARKET_PUBLIC_KEY", "")
DMARKET_SECRET_KEY = os.getenv("DMARKET_SECRET_KEY", "")
