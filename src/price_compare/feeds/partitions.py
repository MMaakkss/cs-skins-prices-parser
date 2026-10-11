"""Daily partitions of the ``sales`` table.

Postgres has no automatic partition creation and pg_partman is not in the
alpine image, so the feed keeps its own window: every day from the retention
cutoff to a few days ahead exists, everything older is dropped. Dropping a
partition is instant and leaves no bloat, which is the point of partitioning.

Partitions are named ``sales_YYYYMMDD`` and bounded by UTC midnights.
"""
import logging
import re
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

PARENT = "sales"
# Sales from a few days ahead are clock skew at most; this only has to cover
# the time until the next maintenance run.
DAYS_AHEAD = 3
_NAME = re.compile(rf"^{PARENT}_(\d{{8}})$")


def oldest_kept_day(today: date, retention_days: int) -> date:
    return today - timedelta(days=retention_days - 1)


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def maintain(conn: Connection, today: date, retention_days: int) -> tuple[int, int]:
    """Create the missing partitions in the window, drop the ones past it.

    Returns ``(created, dropped)``.
    """
    first = oldest_kept_day(today, retention_days)
    last = today + timedelta(days=DAYS_AHEAD)

    existing = {}
    for (name,) in conn.execute(text(
        "SELECT c.relname FROM pg_inherits i "
        "JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_class p ON p.oid = i.inhparent "
        "WHERE p.relname = :parent"
    ), {"parent": PARENT}):
        match = _NAME.match(name)
        if match:
            existing[datetime.strptime(match.group(1), "%Y%m%d").date()] = name

    dropped = 0
    for day, name in sorted(existing.items()):
        if day < first:
            conn.execute(text(f'DROP TABLE "{name}"'))
            dropped += 1

    created = 0
    day = first
    while day <= last:
        if day not in existing:
            conn.execute(text(
                f'CREATE TABLE "{PARENT}_{day:%Y%m%d}" PARTITION OF "{PARENT}" '
                f"FOR VALUES FROM ('{day_start(day).isoformat()}') "
                f"TO ('{day_start(day + timedelta(days=1)).isoformat()}')"
            ))
            created += 1
        day += timedelta(days=1)

    if created or dropped:
        logger.info(
            "sales partitions: created %d, dropped %d (window %s..%s)",
            created, dropped, first, last,
        )
    return created, dropped
