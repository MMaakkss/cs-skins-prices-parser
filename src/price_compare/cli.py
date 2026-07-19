import argparse
import logging
import os
import sys

from price_compare.config import PROXY_ENABLED, PROXY_LIST_FILE
from price_compare.parsers.proxy import ProxyPool
from price_compare.parsers.steam import SteamParser
from price_compare.parsers.dmarket import DMarketParser

logger = logging.getLogger("price_compare")


def _build_proxy_pool(no_proxy: bool):
    """Load the proxy pool unless disabled.

    Enabled when PROXY_ENABLED is true, or (when unset) the list file exists.
    The --no-proxy flag always wins.
    """
    if no_proxy or PROXY_ENABLED is False:
        logger.info("Proxy disabled")
        return None

    if PROXY_ENABLED is None and not os.path.exists(PROXY_LIST_FILE):
        logger.info("Proxy disabled (no %s)", PROXY_LIST_FILE)
        return None

    try:
        pool = ProxyPool.from_file(PROXY_LIST_FILE)
    except OSError as e:
        logger.warning("Could not load proxy list %s: %s", PROXY_LIST_FILE, e)
        return None

    if not len(pool):
        logger.warning("Proxy list %s is empty; running without proxies", PROXY_LIST_FILE)
        return None

    logger.info("Proxy pool: %d proxies from %s", len(pool), PROXY_LIST_FILE)
    return pool


def cmd_parse(args):
    parser_map = {
        "steam": SteamParser,
        "dmarket": DMarketParser,
    }

    parser_cls = parser_map.get(args.marketplace)
    if not parser_cls:
        logger.error(
            "Unknown marketplace: %s. Available: %s",
            args.marketplace, ", ".join(parser_map),
        )
        sys.exit(1)

    filters = {}
    if args.exterior:
        filters["exterior"] = args.exterior
    if args.weapon:
        filters["weapon"] = args.weapon
    if args.search:
        filters["search"] = args.search
    if args.price_min is not None:
        filters["price_min"] = args.price_min
    if args.price_max is not None:
        filters["price_max"] = args.price_max

    proxy_pool = _build_proxy_pool(args.no_proxy)

    # DMarket uses a signed, per-account API — rate limits are per API key, not
    # per IP, and rotating one key across many residential IPs can trip
    # anti-fraud. So it always goes direct; the proxy pool is for Steam.
    if args.marketplace == "dmarket" and proxy_pool is not None:
        logger.info("DMarket uses API keys; proxy pool not applied to it")
        proxy_pool = None

    parser = parser_cls(currency=args.currency, proxy_pool=proxy_pool)
    logger.info("Parsing %s (count=%s, filters=%s)...", args.marketplace, args.count, filters)

    results = parser.run(filters=filters, count=args.count)
    logger.info("Saved %d items to database.", len(results))


def cmd_prices(args):
    from price_compare.db.models import Item, Marketplace, PriceRecord
    from price_compare.db.session import SessionLocal

    session = SessionLocal()
    try:
        query = session.query(PriceRecord, Item, Marketplace).join(
            Item, PriceRecord.item_id == Item.id
        ).join(
            Marketplace, PriceRecord.marketplace_id == Marketplace.id
        ).order_by(PriceRecord.recorded_at.desc())

        if args.name:
            query = query.filter(Item.market_hash_name.ilike(f"%{args.name}%"))
        if args.marketplace:
            query = query.filter(Marketplace.name == args.marketplace)

        query = query.limit(args.limit)
        rows = query.all()

        if not rows:
            print("No price records found.")
            return

        print(f"{'Item':<45} {'Market':<10} {'Price':>10} {'Volume':>8} {'Date'}")
        print("-" * 95)
        for record, item, marketplace in rows:
            print(
                f"{item.market_hash_name:<45} "
                f"{marketplace.name:<10} "
                f"{record.price:>9.2f}{record.currency} "
                f"{record.volume or '-':>8} "
                f"{record.recorded_at:%Y-%m-%d %H:%M}"
            )
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="CS2 Skin Price Comparator")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command")

    # parse command
    parse_cmd = subparsers.add_parser("parse", help="Parse prices from a marketplace")
    parse_cmd.add_argument("marketplace", choices=["steam", "dmarket"], help="Marketplace to parse")
    parse_cmd.add_argument("--count", type=int, default=100, help="Number of items to fetch")
    parse_cmd.add_argument("--exterior", help="Filter by exterior (FN, MW, FT, WW, BS)")
    parse_cmd.add_argument("--weapon", help="Filter by weapon type (e.g. ak47, m4a1)")
    parse_cmd.add_argument("--search", help="Search query")
    parse_cmd.add_argument("--price-min", type=float, help="Min price in dollars (e.g. 1.5)")
    parse_cmd.add_argument("--price-max", type=float, help="Max price in dollars (e.g. 50.0)")
    parse_cmd.add_argument("--currency", default="USD", help="Currency (USD, EUR, RUB)")
    parse_cmd.add_argument("--no-proxy", action="store_true", help="Disable the proxy pool for this run")
    parse_cmd.set_defaults(func=cmd_parse)

    # prices command
    prices_cmd = subparsers.add_parser("prices", help="Show saved prices")
    prices_cmd.add_argument("name", nargs="?", help="Filter by item name (partial match)")
    prices_cmd.add_argument("--marketplace", help="Filter by marketplace")
    prices_cmd.add_argument("--limit", type=int, default=50, help="Max results")
    prices_cmd.set_defaults(func=cmd_prices)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
