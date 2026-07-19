import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/price_compare",
)

# Steam market endpoints tolerate ~1 request / 4s per IP; keep a safe margin.
STEAM_REQUEST_DELAY = float(os.getenv("STEAM_REQUEST_DELAY", "4.0"))
DMARKET_REQUEST_DELAY = float(os.getenv("DMARKET_REQUEST_DELAY", "1.0"))

# DMarket Trading API keys (required — DMarket's market API needs signed requests).
DMARKET_PUBLIC_KEY = os.getenv("DMARKET_PUBLIC_KEY", "")
DMARKET_SECRET_KEY = os.getenv("DMARKET_SECRET_KEY", "")

# --- Proxy pool ---
# Path to the file with one `user:pass@host:port` proxy per line.
PROXY_LIST_FILE = os.getenv("PROXY_LIST_FILE", str(PROJECT_ROOT / "proxy_list.txt"))
# Seconds a proxy stays on cooldown after a 429 / connection error.
PROXY_COOLDOWN = float(os.getenv("PROXY_COOLDOWN", "60.0"))
# Steam bans an IP for hours after too many market requests, so a proxy that
# hits 429 there is parked much longer — effectively dropped for the run.
STEAM_PROXY_COOLDOWN = float(os.getenv("STEAM_PROXY_COOLDOWN", "1800.0"))
# Explicit on/off override. When unset, proxies auto-enable if the file exists.
_proxy_enabled_env = os.getenv("PROXY_ENABLED")
PROXY_ENABLED = (
    _proxy_enabled_env.strip().lower() in ("1", "true", "yes", "on")
    if _proxy_enabled_env is not None
    else None
)
