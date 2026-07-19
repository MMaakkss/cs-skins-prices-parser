# Price Compare

A CLI utility for collecting **Counter-Strike 2** skin prices from several trading
marketplaces and accumulating price history in PostgreSQL.

Currently supported:

- **Steam Community Market** — `steamcommunity.com/market`
- **DMarket** — `api.dmarket.com`

The architecture is designed for easy addition of new marketplaces: just inherit from
`BaseParser` and implement a single method, `fetch_listings()`.

---

## Features

- Parsing prices from Steam and DMarket with pagination and retries on errors.
- Filters: exterior (wear), weapon type, search query, price range, count.
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
| `STEAM_PROXY_COOLDOWN`   | Proxy parking after a Steam 429 (sec)        | `1800.0` |
| `DMARKET_REQUEST_DELAY`  | Delay between requests to DMarket (sec)      | `1.0`   |
| `DMARKET_PUBLIC_KEY`     | Trading API public key (64 hex)              | — (required for DMarket) |
| `DMARKET_SECRET_KEY`     | Trading API secret key (128 hex)             | — (required for DMarket) |
| `PROXY_LIST_FILE`        | Path to the proxy list                       | `proxy_list.txt` |
| `PROXY_COOLDOWN`         | Proxy cooldown after a 429/error (sec)       | `60.0`  |
| `PROXY_ENABLED`          | Explicit on/off override (`true`/`false`)    | based on file presence |

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
```

Arguments:

| Argument        | Description                                          |
|-----------------|------------------------------------------------------|
| `marketplace`   | `steam` or `dmarket` (required)                      |
| `--count`       | How many items to collect (default 100)              |
| `--exterior`    | Wear: `FN`, `MW`, `FT`, `WW`, `BS` (Steam only)      |
| `--weapon`      | Weapon type, e.g. `ak47`, `m4a1` (Steam only)        |
| `--search`      | Search query by name                                 |
| `--price-min`   | Min price in dollars, e.g. `1.5`                     |
| `--price-max`   | Max price in dollars, e.g. `50.0`                    |
| `--no-proxy`    | Disable the proxy pool for this run                  |

All prices are collected and stored in **USD**.

### `prices` command — view saved data

```bash
python -m price_compare prices                       # last 50 records
python -m price_compare prices "AK-47" --marketplace steam --limit 20
```

| Argument        | Description                                   |
|-----------------|-----------------------------------------------|
| `name`          | Partial filter by item name                   |
| `--marketplace` | Filter by marketplace (`steam`, `dmarket`)    |
| `--limit`       | Max rows (default 50)                         |

### Logging

Diagnostic messages are emitted via `logging` (level `INFO`).
The `-v` / `--verbose` flag enables the `DEBUG` level:

```bash
python -m price_compare -v parse steam --count 50
```

---

## Notes and limitations

- **DMarket requires Trading API keys.** DMarket's public market API has moved to
  signed requests (Ed25519). Generate a key pair at `dmarket.com → Settings →
  Trading API` and set `DMARKET_PUBLIC_KEY` / `DMARKET_SECRET_KEY` in `.env`. Without
  keys, DMarket parsing returns an empty result with a message in the log. Proxies are
  not used for DMarket — limits are counted per account, and requests go directly.
- **All prices are USD.** Steam is always queried with `currency=1, cc=US`, DMarket
  returns USD natively. There is no currency selection and no conversion.
- **Steam requires "browser-like" requests.** The market endpoints (`search/render`)
  return `429` for "bare" requests (with only a `User-Agent`). A full set of browser
  headers is required (`Accept-*`, `Referer`, `X-Requested-With`, `Sec-*` / `sec-ch-ua`) —
  these are set in `SteamParser`. Additionally, Steam rate-limits by IP (~1 request /
  4 sec): `STEAM_REQUEST_DELAY` holds a pause between pages, and a proxy that catches a
  `429` is parked for `STEAM_PROXY_COOLDOWN` seconds, yielding to another IP from the
  pool. With residential proxies and browser headers, Steam collection works reliably.
- Running multiple marketplaces in parallel is supported at the PostgreSQL level
  (multiple writers simultaneously).

---

## Proxy pool

To work around Steam's request-rate limit (IP-based bans), the parser can distribute
requests across a pool of proxies with rotation.

- **File format** — one proxy per line: `user:pass@host:port`
  (HTTP proxy; the `http://` scheme is added automatically). Empty lines and lines
  starting with `#` are ignored.
- **Enabling** — automatic if the `PROXY_LIST_FILE` file exists. To disable it for a
  specific run use the `--no-proxy` flag; globally, use `PROXY_ENABLED=false`.
- **How it works** — before each HTTP request a random proxy is picked. On a `429`
  response or a connection error, the proxy goes into cooldown for `PROXY_COOLDOWN`
  seconds, and the request is immediately retried through another IP. This eliminates
  long stalls on a single address at large `--count` values.
- **Geo** — every exit IP has the region of its proxy subscription; this does not
  affect prices (Steam is always queried in USD via fixed `currency`/`cc`, DMarket
  is always USD).

> ⚠️ The proxy list file contains credentials and is added to `.gitignore` —
> do not commit it.

---

## Project structure

```
price_compare/
├── alembic/                     # DB migrations
│   └── versions/
├── src/price_compare/
│   ├── cli.py                   # argument parsing, parse/prices commands
│   ├── config.py                # reading .env
│   ├── db/
│   │   ├── models.py            # Marketplace, Item, PriceRecord
│   │   └── session.py           # engine + SessionLocal
│   └── parsers/
│       ├── base.py              # BaseParser: DB persistence, name normalization
│       ├── steam.py             # SteamParser
│       └── dmarket.py           # DMarketParser
├── docker-compose.yml           # PostgreSQL
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
  `price_type` (`ask`; `bid` reserved for a future phase), `recorded_at`), linked to an
  item and a marketplace. A new record on each run.

---

## Adding a new marketplace

1. Create `src/price_compare/parsers/<name>.py` with a class that inherits from `BaseParser`.
2. Set `marketplace_name` and `base_url`, and implement `fetch_listings()`, which returns
   a list of dicts with the keys `market_hash_name`, `price` (USD), and optionally
   `weapon`, `skin_name`, `exterior`, `icon_url`, `volume`, `stattrak`, `souvenir`.
3. Register the class in `parser_map` inside `cli.py` and add it to the `choices` of the
   `marketplace` argument.
