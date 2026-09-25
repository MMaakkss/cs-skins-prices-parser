# Steam market rate limits — measured behaviour

Everything below was measured against the live endpoints on **2026-09-09** and
**2026-09-12**, from one residential IP plus two unrelated residential exit IPs of a
commercial proxy pool. It contradicted every assumption the parser had been built on;
the code has since been changed to match, so read this before changing the Steam parser
again.

## TL;DR

| Assumption the parser was built on | What the measurements show |
|---|---|
| Steam limits `~1 request / 4s` **per IP** | The IP is not in the key at all |
| A 429 is escaped by rotating to another exit IP | The same 429 follows you to every IP |
| The proxy pool is mainly there for Steam | Proxies buy Steam nothing — the pool has since been removed |
| `search/render` returns up to 100 items per page | It returns 10, `count` is ignored |

## 1. The budget is keyed by the exact `Accept-Encoding` string, not by IP

Burned a never-before-used string, `gzip;q=0.83, deflate`, on `priceoverview` from a home
IP — 429 arrived on request #21. Within the next two seconds, with that same string:

- residential exit `46.150.69.192` → **429**
- residential exit `31.40.19.7` → **429**
- a neighbouring string `gzip;q=0.61, deflate`, same home IP → **200**
- the same neighbouring string through the proxy → **200**

So the bucket is shared across the whole internet per string, and three unrelated
networks draw from one counter. Two more properties of the key:

- **Item names are not in it.** 40 *different* `market_hash_name`s under one string still
  hit 429 on request #21.
- **The comparison is literal.** Case and whitespace matter: `gzip,deflate` and
  `gzip, deflate` are two separate budgets.

## 2. Each endpoint has its own budget

| Endpoint | Items per request | Tokens per string from cold | Items per charge |
|---|---|---|---|
| `market/priceoverview` | 1 | 20 (429 on #21) | 20 |
| `market/search/render` | 10 | 100 (429 on #101) | **1000** |

They are independent: after `priceoverview` was burned on a string, `search/render`
answered 200 on that same string. Per item retrieved, `search/render` is **50× cheaper**,
which is the whole argument for building the full-market pass on it.

## 3. `search/render` returns 10 items per page, not 100

`count` is ignored — the response always carries `pagesize: 10`. Verified with
`count=10/20/50/100`, with and without `sort_column`/`sort_dir`, at `start=0` and
`start=500`. The cap used to be 100.

This was an active bug here: the parser asked for `count=100` and advanced
`start += 100` after receiving 10 results, keeping 10 items out of every 100 and
silently skipping ~90% of the market. It now advances by the `pagesize` the response
actually reports, and stops on an empty page.

At 10 items per page the CS2 market (`total_count` ≈ 35,480) is a **3,548-request** pass,
up from 355.

## 4. Recovery is slow, and knocking extends the block

After `search/render` was burned, 60 seconds of silence returned **0** tokens, and
another 60 seconds returned 0 again — while every probe potentially refreshes the block.
This matches the long blocks reported publicly (hours, in the worst reports) and the
independent observation that a quiet stop recovers in tens of seconds whereas polling
every 20s holds the 429 for 10+ minutes.

Practical consequence: **on 429, stop and back off silently.** Fast retries are not
merely useless here, they are the thing that keeps the block alive.

## 5. Library default strings share their budget with the whole internet

The defaults of popular clients are the most contended strings on the platform, so they
behave like a blocklist that changes from day to day:

| `Accept-Encoding` | Used by | 2026-09-09 | 2026-09-12 |
|---|---|---|---|
| `gzip, deflate` | urllib3 | 429 always | 429 always |
| `gzip, deflate, br` | **requests (this repo)** | 429 always | 429 always |
| `gzip, deflate, br, zstd` | httpx, Chrome | 429 always | 200 |
| `gzip,deflate` (no space) | nobody | 200 | 200 |

`requests` sends `gzip, deflate, br` and this repo never overrides it, so the Steam
parser has been drawing from a permanently exhausted bucket. Its 429s are mostly other
people's traffic, not its own pace.

Setting one project-specific string gives this repo a private budget. Note the obvious
line: *one* string, as a way to stop sharing a counter with every scraper on the planet —
not a rotating set of strings to multiply the quota.

## 6. Proxies do not help Steam

Follows from §1: the exit IP is not in the key, so rotating it cannot restore a burned
budget, and the "rotate the proxy and retry immediately" path was a no-op against Steam
that also happened to be the retry pattern §4 warns about — it parked a proxy that was
never the problem. The pool had no other users left and has been removed.

DMarket and market.csgo.com are unaffected by any of this — they are rate-limited per API
key and keep their own settings.

## 7. Dead end: the csgotrader bulk dump

`https://prices.csgotrader.app/latest/steam.json` (34,413 CS2 items, one 541 KB request)
looks like an obvious replacement for the whole Steam parser. It is not usable:

- Two snapshots three days apart are **byte-identical** — 0 of 34,413 items changed —
  even though `Last-Modified` advances every hour. The feed behind it is frozen.
- Against live `priceoverview` it overstates consistently: Dreams & Nightmares Case 1.84
  vs median 1.64 (+12%), AK-47 Redline FT 43.56 vs 37.31 (+17%), Sticker ZywOo Rio 2022
  +22%.
- `prices_v6.json` and dated snapshots are gone (301).

The acceptance test for **any** bulk price source, before wiring it in: pull two
snapshots a day or two apart, diff every key to see how many actually moved, and spot
check 5–8 names against `priceoverview`. `Last-Modified` proves nothing. The same test
still has to be run against the paid aggregators (steamdataapi.com, steamwebapi.com) if
they are ever considered.

## What is still unknown

1. **The refill rate of `search/render`** — the one number needed to size a full-market
   pass. 100 tokens from cold is measured; how fast they come back is not. A pass is
   3,548 requests, so the answer is the difference between ~36 minutes and ~1.5 days.
2. **How long a block lasts** once triggered. Known to exceed 2 minutes of silence.
3. Whether the per-string budget varies by time of day, as third-party reports suggest
   for other Steam endpoints.

Item 1 is meant to be answered by the parser itself: it logs a line per request (index,
`start` offset, status), so a full pass on the server either completes — putting a floor
under the refill rate — or records exactly where the 429 landed.
