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

## 8. What ends a pass is a daily cap of ~2,500 requests, not a rate

Two full passes from the server, and the second one falsified the reading of the first.

| | Pass 1 (2026-09-25) | Pass 2 (2026-09-26) |
|---|---|---|
| Pace | 4.30 s per request | **4.80 s per request** |
| Duration | 179 min | 200 min |
| Quiet before the start | hours | **21 hours** |
| Successful requests | 2,497 | 2,500 |
| 429 on request | #2,498 | #2,501 |

Pass 1 was preceded the same day by a 3-request smoke test: 2,497 + 3 = **2,500**. Pass 2
made 2,500 requests and nothing else ran that day. Two days, two paces, the same total.

The first pass alone looked like a refilling bucket: with the 100 tokens of §2 as a
starting credit, `100 + r × 10,745 s = 2,497` gives r ≈ 0.223 req/s, and that model
predicted the failure to within five seconds. It was one point fitted with a line. A
*slower* second pass should then have gone further, and it did not move at all.

So the binding limit is a **quota of ~2,500 requests per day** per `Accept-Encoding`
string on `search/render`, and **pacing does not change how much a day yields**. The
burst limit of §2 is a separate, shorter-term thing; both passes stayed under it, so all
that is known is that one request per 4.30 s does not trip it.

What this costs: 2,500 × 10 items = **25,000 positions a day against a market of
35,525**. A full snapshot cannot be taken in one day through this endpoint — it takes
~1.42 days, which is why the resume point matters and why a second endpoint for the
categories it can serve losslessly (§10) stops being an optimisation and becomes the
only way to a daily snapshot.

Still open on this: whether the window is a calendar day or a rolling 24 hours, and what
the burst limit actually allows — no pace faster than 4.30 s has been tried on the
server, and a faster one would finish the daily quota in less wall-clock time.

## 9. Popularity ordering is unstable deeper in the list

The same pass traversed 24,970 positions and wrote 24,654 priced rows — but only
**19,473 distinct names**. 21% of the requests re-fetched an item already seen, the worst
of them five times over.

`sort_column=popular` reshuffles between requests, so pages overlap; every duplicate is
also an item the pass never reached. Verified on a narrow filter too: one weapon and one
exterior returned 108 positions for 83 distinct names, with page boundaries repeating
from `start=40` onwards, while the first four pages were clean.

**Fixed and confirmed.** The parser now sorts by name (`sort_column=name&sort_dir=asc`).
The next pass wrote 24,792 rows covering 24,766 distinct names — 26 duplicates, 0.10%,
down from 21%. What it costs is one thing:
`--count N` means "the first N alphabetically" rather than "the N most popular". At 21%
waste this was the cheapest improvement available to the pass — worth more than any
endpoint change measured here.

One consequence of the switch: a resume offset means "position N **in this order**", so
the offsets recorded under the old one point somewhere else entirely. `SteamParser`
therefore writes the page order into the resume key, which orphans the old rows instead
of resuming into the wrong part of the market.

## What is still unknown

1. **How long a block lasts** once triggered. Known to exceed 2 minutes of silence; the
   pass of §8 was not probed again afterwards, so the recovery time is still open.
2. Whether the per-string budget varies by time of day, as third-party reports suggest
   for other Steam endpoints. §8 measured one afternoon pass; a night pass at the same
   pace would answer it.
3. Whether the refill rate is steady or bursty. The linear model of §8 fits one pass to
   within five seconds, which is suggestive but not proof.

The refill rate itself — the number this file used to be missing — is answered in §8:
**0.223 req/s**, one request per 4.48 s.
