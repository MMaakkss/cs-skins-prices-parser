import logging
import random
import time

logger = logging.getLogger(__name__)


class ProxyPool:
    """A rotating pool of HTTP proxies with per-proxy cooldown.

    Each entry is a ``user:pass@host:port`` line turned into an
    ``http://user:pass@host:port`` URL. ``get()`` hands out a random proxy that
    is not currently cooling down; a proxy is put on cooldown via ``penalize()``
    after a rate-limit (429) or a connection error so the next request routes
    through a different exit IP.
    """

    def __init__(self, proxies: list[str]):
        self.proxies = [self._normalize(p) for p in proxies]
        self._cooldown: dict[str, float] = {}

    @classmethod
    def from_file(cls, path: str) -> "ProxyPool":
        with open(path, encoding="utf-8") as f:
            lines = [line.strip() for line in f]
        entries = [line for line in lines if line and not line.startswith("#")]
        return cls(entries)

    @staticmethod
    def _normalize(entry: str) -> str:
        """Turn a raw list entry into an ``http://...`` proxy URL."""
        entry = entry.strip()
        if "://" in entry:
            return entry
        return f"http://{entry}"

    def get(self) -> dict | None:
        """Return a ``requests``-style proxies dict for a random live proxy.

        Picks uniformly among proxies not on cooldown. If every proxy is
        cooling down, falls back to a random one (best effort) and warns.
        Returns ``None`` only when the pool is empty.
        """
        if not self.proxies:
            return None

        now = time.monotonic()
        available = [p for p in self.proxies if self._cooldown.get(p, 0.0) <= now]

        if available:
            proxy = random.choice(available)
        else:
            proxy = random.choice(self.proxies)
            logger.warning("All %d proxies on cooldown; reusing one", len(self.proxies))

        return {"http": proxy, "https": proxy}

    def penalize(self, proxies: dict | None, seconds: float) -> None:
        """Put the proxy from a ``get()`` result on cooldown for ``seconds``."""
        if not proxies:
            return
        proxy = proxies.get("https") or proxies.get("http")
        if proxy:
            self._cooldown[proxy] = time.monotonic() + seconds

    def __len__(self) -> int:
        return len(self.proxies)
