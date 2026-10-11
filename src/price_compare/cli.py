import argparse
import logging
import sys

from price_compare.parsers.steam import SteamParser
from price_compare.parsers.dmarket import DMarketParser
from price_compare.parsers.market_csgo import MarketCsgoParser

MARKETPLACES = {
    "steam": SteamParser,
    "dmarket": DMarketParser,
    "market_csgo": MarketCsgoParser,
}

logger = logging.getLogger("price_compare")


def cmd_parse(args):
    parser_cls = MARKETPLACES.get(args.marketplace)
    if not parser_cls:
        logger.error(
            "Unknown marketplace: %s. Available: %s",
            args.marketplace, ", ".join(MARKETPLACES),
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

    # --all overrides --count: pass count=None so the parsers page through
    # every available listing instead of stopping at a fixed number.
    count = None if args.all else args.count

    parser = parser_cls()
    logger.info(
        "Parsing %s (count=%s, filters=%s)...",
        args.marketplace, "all" if count is None else count, filters,
    )

    saved = parser.run(filters=filters, count=count)
    logger.info("Saved %d price records to database.", saved)


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

        print(f"{'Item':<45} {'Market':<10} {'Type':<5} {'Price(USD)':>11} {'Volume':>8} {'Date'}")
        print("-" * 100)
        for record, item, marketplace in rows:
            print(
                f"{item.market_hash_name:<45} "
                f"{marketplace.name:<10} "
                f"{record.price_type:<5} "
                f"${record.price:>9.2f} "
                f"{record.volume or '-':>8} "
                f"{record.recorded_at:%Y-%m-%d %H:%M}"
            )
    finally:
        session.close()


def cmd_listen(args):
    from price_compare.feeds.market_csgo import MarketCsgoSalesFeed

    MarketCsgoSalesFeed().run()


def cmd_sales(args):
    from sqlalchemy import text

    from price_compare.db.session import engine

    # Windows are counted back from now, not by calendar day, so the partition
    # boundaries do not show in the numbers.
    query = text("""
        SELECT i.market_hash_name,
               count(*) FILTER (WHERE s.sold_at > now() - interval '7 days')  AS week,
               count(*)                                                       AS month,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY s.price)
                   FILTER (WHERE s.sold_at > now() - interval '7 days')       AS median_week,
               max(s.sold_at)                                                 AS last_sale
        FROM sales s
        JOIN items i ON i.id = s.item_id
        JOIN marketplaces m ON m.id = s.marketplace_id
        WHERE m.name = :marketplace
          AND s.sold_at > now() - interval '30 days'
          AND (CAST(:name AS text) IS NULL OR i.market_hash_name ILIKE '%' || :name || '%')
        GROUP BY i.market_hash_name
        ORDER BY week DESC, month DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {
            "marketplace": args.marketplace, "name": args.name, "limit": args.limit,
        }).all()

    if not rows:
        print("No sales found.")
        return

    print(f"{'Item':<55} {'7d':>6} {'30d':>6} {'Median 7d':>10} {'Last sale'}")
    print("-" * 100)
    for name, week, month, median_week, last_sale in rows:
        median = f"${median_week:.2f}" if median_week is not None else "-"
        print(f"{name:<55} {week:>6} {month:>6} {median:>10} {last_sale:%Y-%m-%d %H:%M}")


def main():
    parser = argparse.ArgumentParser(description="CS2 Skin Price Comparator")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command")

    # parse command
    parse_cmd = subparsers.add_parser("parse", help="Parse prices from a marketplace")
    parse_cmd.add_argument("marketplace", choices=list(MARKETPLACES), help="Marketplace to parse")
    parse_cmd.add_argument("--count", type=int, default=100, help="Number of items to fetch")
    parse_cmd.add_argument("--all", action="store_true", help="Fetch all available items (ignores --count)")
    parse_cmd.add_argument("--exterior", help="Filter by exterior (FN, MW, FT, WW, BS)")
    parse_cmd.add_argument("--weapon", help="Filter by weapon type (e.g. ak47, m4a1)")
    parse_cmd.add_argument("--search", help="Search query")
    parse_cmd.add_argument("--price-min", type=float, help="Min price in dollars (e.g. 1.5)")
    parse_cmd.add_argument("--price-max", type=float, help="Max price in dollars (e.g. 50.0)")
    parse_cmd.set_defaults(func=cmd_parse)

    # prices command
    prices_cmd = subparsers.add_parser("prices", help="Show saved prices")
    prices_cmd.add_argument("name", nargs="?", help="Filter by item name (partial match)")
    prices_cmd.add_argument("--marketplace", help="Filter by marketplace")
    prices_cmd.add_argument("--limit", type=int, default=50, help="Max results")
    prices_cmd.set_defaults(func=cmd_prices)

    # listen command
    listen_cmd = subparsers.add_parser(
        "listen", help="Record every sale from a marketplace's live feed (runs until stopped)"
    )
    listen_cmd.add_argument("marketplace", choices=["market_csgo"], help="Marketplace feed")
    listen_cmd.set_defaults(func=cmd_listen)

    # sales command
    sales_cmd = subparsers.add_parser("sales", help="Show sales volume over the last 7 / 30 days")
    sales_cmd.add_argument("name", nargs="?", help="Filter by item name (partial match)")
    sales_cmd.add_argument("--marketplace", default="market_csgo", help="Marketplace")
    sales_cmd.add_argument("--limit", type=int, default=50, help="Max results")
    sales_cmd.set_defaults(func=cmd_sales)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
