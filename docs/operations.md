# What a run actually costs — measured on the server

Numbers from the deployed parser, not estimates. Add to this file as more passes
run; rate-limit behaviour belongs in [`steam-rate-limits.md`](steam-rate-limits.md)
instead.

## Cost of one pass, per marketplace

Measured **2026-09-25** on a 3.7 GB VPS, Postgres 18 in the same compose project.

| Marketplace | Requests | Records written | Wall clock | Notes |
|---|---|---|---|---|
| `market_csgo` | 2 | 52,710 | 2 m 11 s | 27,698 ask + 25,084 bid, 72 skipped on a zero price |
| `steam` (narrow: one weapon, one exterior) | 3 | 28 | 13 s | `--count 30`, 2 skipped |
| `steam` (full market, 4.0 s delay, popularity order) | 2,497 | 24,654 | 2 h 59 m | stopped by the daily cap at 70%; 21% of rows were duplicates |
| `steam` (full market, 4.5 s delay, name order) | 2,500 | 24,792 | 3 h 20 m | stopped by the same cap at 70%; 0.10% duplicates |
| `steam` (the same pass resumed three days later) | 1,053 | 10,279 | 1 h 24 m | reached the end; `parser_state` reset itself to 0 |

`market_csgo` spent **2 seconds** of those 131 on the network — both price dumps
arrive in two requests. Everything else was database work.

## Write throughput: ~400 rows/s, and why

52,710 records took ~2 minutes. The cost is one `SELECT` per listing:
`_persist()` looks up each `market_hash_name` to decide between insert and
backfill, so a `market_csgo` pass issues ~52,000 point queries.

That is fine at one pass a day and not worth optimising yet. It becomes worth
optimising when passes get frequent or marketplaces are added, and the fix is
known: load the existing names for a batch in one query instead of one per row.

For Steam the cost is invisible — ~35,000 rows spread across ~3,548 page
commits, against four hours of deliberately rate-limited fetching.

## A Steam pass does not fit in one day

Both attempts stopped at ~2,500 requests — 25,000 of 35,525 positions — regardless of
pace. That is a daily quota, not a rate ([`steam-rate-limits.md`](steam-rate-limits.md)
§8), so slowing down or speeding up changes only how long the day's quota takes to spend.

Operationally:

- A full market snapshot takes **two days**: ~2,500 requests, then ~1,053 the next day
  from the recorded offset. Nothing is re-fetched.
- Slowing down buys nothing. 4.5 s is kept because it is known not to trip the separate
  burst limit, not because it stretches the quota.
- Daily coverage of the whole market needs requests from somewhere other than
  `search/render` — which is what makes the 100-items-per-request endpoint worth
  revisiting for the categories it serves losslessly.

## A complete market snapshot, and what it cost

The first full alphabetical snapshot was finished on 2026-09-29, in two sessions split by
the daily quota:

```
2,500 requests + 1,053 requests = 3,553   (= 35,524 positions / 10 per page)
24,792 rows    + 10,279 rows    = 35,071 price records
208 skipped    + 245 skipped    =    453 items priced at zero
35,071 + 453 = 35,524 — every position traversed is accounted for
```

35,039 distinct names against 32 duplicate writes: **0.09%**. The resume point carried
the offset across three days and a container that no longer existed, then reset itself to
0 on completion, which is what makes "two sessions" a detail rather than an operation.

Wall clock was 4 h 44 m of requesting. The gap between the sessions was not: the quota
has to refill, and that is the real cost of a full snapshot — **1.42 days of quota**, not
five hours of runtime.

## Database growth

After one `market_csgo` pass and a complete Steam pass: **36,402 items, 45 MB**, holding
59,753 Steam price records (several snapshots per item by now) and 52,710 from
`market_csgo`.
A daily full pass of both marketplaces adds roughly 5–8 MB, so on the order of
2 GB a year. Worth a `pg_dump` in cron long before it is worth worrying about
disk.

Prices are stored as `Numeric(14,4)`, and the tail of the market needs those
four digits: `market_csgo` buy orders go down to $0.0010, with ~400 records
below a cent.

## A pass is written as it runs

Listings are committed page by page, together with the resume offset, in one
transaction per batch. Verified on a narrow full pass: rows accumulated
19 → 46 → 65 → 85 → 103 while the pass was still running, `parser_state.next_start`
advanced 20 → 50 → 70 → 90 alongside them, and reset to 0 when the pass finished.

The consequence worth remembering: **a failure of any kind — 429, crash, OOM
kill, Ctrl-C, a dropped network — costs at most the page in flight.** The next
`--all` run over the same filters continues from the recorded offset. Passes
limited with `--count N` deliberately neither read nor write that offset.
