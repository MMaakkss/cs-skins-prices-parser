import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/price_compare",
)

# Steam's market limit is not per IP: the budget is keyed by the exact
# Accept-Encoding string and shared globally (docs/steam-rate-limits.md).
# The delay is a pace that keeps the pass inside that budget; the refill rate
# it should be matched to is still being measured on the server.
STEAM_REQUEST_DELAY = float(os.getenv("STEAM_REQUEST_DELAY", "4.0"))
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
