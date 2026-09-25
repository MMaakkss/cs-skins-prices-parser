# Price Compare

A CLI utility for collecting **Counter-Strike 2** skin prices from several trading
marketplaces and accumulating price history in PostgreSQL.

Currently supported:

- **Steam Community Market** — `steamcommunity.com/market`
- **DMarket** — `api.dmarket.com`
- **market.csgo.com** (TM Market) — `market.csgo.com/api/v2` (asks **and** buy orders)

The architecture is designed for easy addition of new marketplaces: just inherit from
`BaseParser` and implement a single method, `fetch_listings()`.

---

## Features

- Parsing prices from Steam, DMarket and market.csgo.com with pagination and retries
  on errors.
- Filters: exterior (wear), weapon type, search query, price range, count.
- Both sides of the book where a marketplace publishes them: lowest ask (`ask`) and
  highest buy order (`bid`) — market.csgo.com provides both.
- **History accumulation**: each run adds a new price snapshot for an item —
  past values are not overwritten, which makes it possible to analyze trends.
- Item name normalization (a unified form without altering the name itself), so that
  the same skin from different marketplaces maps to a single record.
- Skipping and logging of records with an invalid (zero/empty) price.
- Storage in PostgreSQL via SQLAlchemy, migrations via Alembic.
- Viewing saved prices from the command line.

---

## Tech stack

| Purpose        | Tool                                |
|----------------|-------------------------------------|
| Language       | Python 3.10+                        |
| HTTP client    | `requests`                          |
| Database       | PostgreSQL 16                       |
| ORM            | SQLAlchemy 2.x                      |
| Migrations     | Alembic                             |
| DB driver      | `psycopg2-binary`                   |
| Configuration  | `python-dotenv` + environment vars  |
| Infrastructure | Docker Compose (PostgreSQL)         |

---

## Installation and setup

### 1. Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment variables

Copy the example and edit it if needed:

```bash
cp .env.example .env
```

| Variable                 | Purpose                                     | Default |
|--------------------------|---------------------------------------------|---------|
| `DATABASE_URL`           | PostgreSQL connection string                | `postgresql://postgres:postgres@localhost:5432/price_compare` |
| `STEAM_REQUEST_DELAY`    | Delay between requests to Steam (sec)        | `4.0`   |
| `STEAM_ACCEPT_ENCODING`  | The `Accept-Encoding` string Steam's rate limit is keyed by | `gzip;q=0.97, deflate` |
| `DMARKET_REQUEST_DELAY`  | Delay between requests to DMarket (sec)      | `1.0`   |
| `DMARKET_PUBLIC_KEY`     | Trading API public key (64 hex)              | — (required for DMarket) |
| `DMARKET_SECRET_KEY`     | Trading API secret key (128 hex)             | — (required for DMarket) |
| `MARKET_CSGO_REQUEST_DELAY` | Min interval between market.csgo.com requests (sec) | `0.25` |
| `MARKET_CSGO_API_KEY`    | market.csgo.com key — **not used** by the parser | — |

### 3. Database

Start PostgreSQL in Docker and apply the migrations:

```bash
docker compose up -d          # starts the price_compare_db container
alembic upgrade head          # creates the tables
```

---

## Usage

The project runs as a module. The `src` directory must be on `PYTHONPATH`:

```bash
export PYTHONPATH=src
python -m price_compare --help
```

### `parse` command — collect prices

```bash
python -m price_compare parse steam --count 200 --weapon ak47 --exterior FT
python -m price_compare parse dmarket --search "AWP | Asiimov" --price-max 120
python -m price_compare parse market_csgo --count 500
```

Arguments:

| Argument        | Description                                          |
|-----------------|------------------------------------------------------|
| `marketplace`   | `steam`, `dmarket` or `market_csgo` (required)       |
| `--count`       | How many items to collect (default 100)              |
| `--exterior`    | Wear: `FN`, `MW`, `FT`, `WW`, `BS` (Steam, market.csgo.com) |
| `--weapon`      | Weapon type, e.g. `ak47`, `m4a1` (Steam, market.csgo.com) |
| `--search`      | Search query by name                                 |
| `--price-min`   | Min price in dollars, e.g. `1.5`                     |
| `--price-max`   | Max price in dollars, e.g. `50.0`                    |

All prices are collected and stored in **USD**.

### `prices` command — view saved data

```bash
python -m price_compare prices                       # last 50 records
python -m price_compare prices "AK-47" --marketplace steam --limit 20
```

| Argument        | Description                                   |
|-----------------|-----------------------------------------------|
| `name`          | Partial filter by item name                   |
| `--marketplace` | Filter by marketplace (`steam`, `dmarket`, `market_csgo`) |
| `--limit`       | Max rows (default 50)                         |

### Logging

Diagnostic messages are emitted via `logging` (level `INFO`).
The `-v` / `--verbose` flag enables the `DEBUG` level:

```bash
python -m price_compare -v parse steam --count 50
```

---

## Deployment

The parser runs on a VPS as its own compose project (`cs-parser`): GitHub
Actions builds the image, pushes it to GHCR, then pins that exact sha on the
server over SSH. The project is self-contained — its own Postgres, volume and
network — so the same compose file runs unchanged on a laptop.

- `Dockerfile` — the image. A pass is a batch job, so the container runs one CLI
  invocation and exits.
- `deploy/docker-compose.yml` — what lives at `/home/ubuntu/cs-parser/` on the
  VPS: `postgres` (kept running) and `parser` (run per pass, then gone).
- `deploy/.env.example` — template for `/home/ubuntu/cs-parser/.env`.
- `.github/workflows/deploy.yml` — manual trigger (Actions → deploy → Run
  workflow). Builds, pushes, pins the sha into `.env` and runs
  `alembic upgrade head`. It does **not** start a pass.

There is one environment, not a prod/stage pair. A pass is priced in Steam's
rate-limit budget, and that budget is keyed by the `Accept-Encoding` string and
shared globally — a second environment would either draw down the first one's
budget or need a second string, which is the fan-out the measurements warn
against ([`docs/steam-rate-limits.md`](docs/steam-rate-limits.md) §1). Schema
changes and parser logic are verified against a throwaway local stack instead:
the compose file runs as-is on a laptop.

Repository secrets the workflow needs: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`.

### Running a pass

Deploying only updates the image and the schema. A pass is started by hand,
because a full one is ~3,548 requests at `STEAM_REQUEST_DELAY` seconds apart:

```bash
ssh <your-server>
cd /home/ubuntu/cs-parser
COMPOSE="docker compose -p cs-parser --env-file .env -f docker-compose.yml"

$COMPOSE run --rm parser parse steam --all          # full market, ~4 hours
$COMPOSE run --rm parser parse steam --count 100    # a quick slice off the top
$COMPOSE run --rm parser parse market_csgo --all    # two requests, seconds
$COMPOSE run --rm parser prices "AK-47" --limit 20  # read back what was saved
```

A pass cut short by a `429` keeps everything it collected and records where it
stopped, so the next `--all` run continues from that offset instead of
restarting at 0.

Each request writes a line — `steam request #N: start=… status=200 items=10` —
so `docker logs` (or whatever collector the host runs) carries the raw material
for sizing a safe request delay.

---

## Notes and limitations

- **DMarket requires Trading API keys.** DMarket's public market API has moved to
  signed requests (Ed25519). Generate a key pair at `dmarket.com → Settings →
  Trading API` and set `DMARKET_PUBLIC_KEY` / `DMARKET_SECRET_KEY` in `.env`. Without
  keys, DMarket parsing returns an empty result with a message in the log. Limits are
  counted per account, and requests go directly.
- **market.csgo.com needs no API key.** It publishes its whole CS2 price list as two
  static USD files — `api/v2/prices/USD.json` (lowest ask per item, ~27k items) and
  `api/v2/prices/orders/USD.json` (highest buy order per item, ~26k items) — and both
  are public. Its keyed endpoints (`search-item-by-hash-name` and friends) quote in the
  **account's own currency** (RUB by default) in minor units, which the USD-only model
  cannot store, so they are not used and `MARKET_CSGO_API_KEY` stays unused.
  Consequences of the static-dump design:
  - A run is exactly **two requests**, so there is no pagination and the marketplace's
    hard limit (**more than 5 req/s deletes the API key**) is never approached. The
    parser still enforces `MARKET_CSGO_REQUEST_DELAY` between requests, retries
    included.
  - Filters (`--search`, `--weapon`, `--exterior`, `--price-min/max`) are applied
    **locally** to the downloaded dump; the files take no query parameters.
  - `--count N` returns the N items with the most **open buy orders**, not an
    alphabetical slice. Buy orders measure real demand; the number of items on sale
    measures the opposite, since worthless skins are the ones that pile up unsold.
    Items nobody bids on are ordered by price, so the tail is valuable-but-illiquid
    rather than sub-cent dust.
  - This parser writes **two records per item** — one `ask` and one `bid` — where
    Steam and DMarket write one `ask`. Buy orders are only stored for items that
    survived the filters, so both sides always describe the same item set.
  - The price dumps carry no images, so `icon_url` is left for another marketplace
    to backfill.
- **All prices are USD.** Steam is always queried with `currency=1, cc=US`, DMarket
  returns USD natively, market.csgo.com is read from its USD files. There is no
  currency selection and no conversion.
- **Steam's rate limit is keyed by `Accept-Encoding`, not by IP.** The budget for
  `search/render` belongs to the exact header string and is shared by everyone sending
  it, so the `requests` default (`gzip, deflate, br`) draws from a permanently exhausted
  bucket, while any literally different value is a private one — that is what
  `STEAM_ACCEPT_ENCODING` is. Two rules follow: keep the value **stable** (it is this
  project's bucket identity, not something to rotate), and do not copy it into the other
  parsers, which are limited per API key. Proxies are of no use here — the exit IP is not
  part of the key. Measurements: [`docs/steam-rate-limits.md`](docs/steam-rate-limits.md).
- **A Steam pass stops on the first `429` and resumes later.** Retrying only refreshes
  the block, so the parser ends the pass, keeps everything collected so far, and records
  the page offset in the `parser_state` table; the next full run (`--all`) continues from
  it, and starts over at 0 once a pass has been completed. `--count N` runs are slices off
  the top and neither read nor move that offset. `STEAM_REQUEST_DELAY` paces the pass —
  `search/render` returns 10 items per page, so the whole CS2 market is ~3,500 requests.
- Running multiple marketplaces in parallel is supported at the PostgreSQL level
  (multiple writers simultaneously).

---

## Project structure

```
price_compare/
├── alembic/                     # DB migrations
│   └── versions/
├── docs/
│   └── steam-rate-limits.md     # measured behaviour of Steam's limits
├── src/price_compare/
│   ├── cli.py                   # argument parsing, parse/prices commands
│   ├── config.py                # reading .env
│   ├── db/
│   │   ├── models.py            # Marketplace, Item, PriceRecord, ParserState
│   │   └── session.py           # engine + SessionLocal
│   └── parsers/
│       ├── base.py              # BaseParser: DB persistence, name normalization
│       ├── steam.py             # SteamParser
│       ├── dmarket.py           # DMarketParser
│       └── market_csgo.py       # MarketCsgoParser (ask + bid)
├── deploy/
│   ├── docker-compose.yml       # the VPS compose project (cs-parser)
│   └── .env.example             # template for the server's .env
├── .github/workflows/
│   └── deploy.yml               # build -> GHCR -> pin sha on the VPS
├── Dockerfile
├── docker-compose.yml           # PostgreSQL (local development)
├── alembic.ini
├── requirements.txt
└── .env.example
```

### Data model

- **marketplaces** — marketplaces (`id`, `name`, `url`) plus a fee reference the API
  reads: `sell_fee_percent`, `buy_fee_percent`, `payout_withdrawable`
  (Steam wallet funds are not withdrawable → `false`).
- **items** — unique items (`market_hash_name`, weapon, skin, exterior, StatTrak/Souvenir
  flags, `icon_url`).
- **price_records** — price snapshots over time (`price` as USD `Numeric`, `volume`,
  `price_type` (`ask` = lowest listing, `bid` = highest buy order), `recorded_at`),
  linked to an item and a marketplace. A new record on each run. Steam and DMarket
  produce `ask` only; market.csgo.com produces both.
- **parser_state** — where a paginated pass stopped (`marketplace`, `filters_key`,
  `next_start`), so a Steam run cut short by a rate limit resumes instead of restarting.

---

## Adding a new marketplace

1. Create `src/price_compare/parsers/<name>.py` with a class that inherits from `BaseParser`.
2. Set `marketplace_name` and `base_url`, and implement `fetch_listings()`, which returns
   a list of dicts with the keys `market_hash_name`, `price` (USD), and optionally
   `weapon`, `skin_name`, `exterior`, `icon_url`, `volume`, `stattrak`, `souvenir`.
3. Register the class in `parser_map` inside `cli.py` and add it to the `choices` of the
   `marketplace` argument.
