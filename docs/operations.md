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
| `steam` (full market, attempt at a 4 s delay) | 2,497 | 24,654 | 2 h 59 m | stopped by a 429 at 70%; see [`steam-rate-limits.md`](steam-rate-limits.md) §8 |

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

## A Steam pass does not fit in one sitting at a 4 s delay

The first full attempt reached `start=24970` of ~35,520 before the budget ran out. That
is not a failure mode to fix in code — the pace was wrong, and §8 of the rate-limit notes
now gives the arithmetic for a pace that finishes. Two operational consequences:

- The tail of the market is priced by the **next** run, which resumes from the recorded
  offset. Nothing has to be re-fetched.
- Of the 24,654 rows written, only 19,473 were distinct items: the endpoint's popularity
  ordering repeats items across pages (§9). Budget spent on duplicates is budget the tail
  never sees.

## Database growth

After one `market_csgo` pass and 70% of a Steam pass: **32,634 items, 36 MB**.
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
