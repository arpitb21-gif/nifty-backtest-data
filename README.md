# nifty-backtest-data

Self-contained reference. If you are a fresh Claude session with no prior
conversation history, this file tells you exactly what data exists in this
repository, its real date coverage, and which backtest window to actually
use. You do not need the original conversation that built this.

---

## Start here: this repo is only half the picture

This README covers **data only**. The overarching plan, decisions, strategy
specs, and current task status live in a separate PDF — **not in this repo**
— called `Phase1_Backtest_Roadmap.pdf`. Read that first if you want the full
picture; come back to this file for data specifics.

This repo also contains a **`pipeline/`** folder — the actual Python code
(Task C's data pipeline, Task D's cost model) that operates on the data
described below. See `pipeline/README.md` for what it does and how to run
it. It is NOT just data; treat this repo as data + code together.

---

## Official backtest window (use this, not the raw ranges below)

To keep every strategy tested on an identical, comparable period, all 15
strategies in the Phase 1 roadmap use this single window, regardless of
how far the raw data underneath happens to extend:

| | Period |
|---|---|
| **Tune (in-sample)** | 2021-05-27 → 2025-03-31 |
| **OOS (out-of-sample)** | 2025-04-01 → 2026-07-02 |

This is the intersection of every dataset's coverage (see below) — the
latest start date and earliest end date across all 9 data types. A signal
needing a lookback (e.g. 252-day IV rank, 52-week high) may reach back
before 2021-05-27 into the raw data as a warm-up period; that warm-up is
not itself part of the tested window.

---

## The 9 data types

| # | Data | Path | Raw range | Rows/files |
|---|---|---|---|---|
| 1 | Daily NIFTY spot | `data/daily/nifty_spot_daily_2020-04-01_to_2026-09-15.csv` | 2020-04-01 → 2026-09-15 | 1,587 rows |
| 2 | Daily BANKNIFTY spot | `data/daily/banknifty_spot_daily_2020-04-01_to_2026-09-15.csv` | 2020-04-01 → 2026-09-15 | 1,593 rows |
| 3 | Daily India VIX | `data/daily/india_vix_daily_2020-04-01_to_2026-09-15.csv` | 2020-04-01 → 2026-09-15 | 1,597 rows |
| 4 | Daily F&O bhavcopy (NIFTY + BANKNIFTY futures & options, all strikes/expiries) | `data/daily/fno_bhavcopy/<year>/` | 2020-04-13 → 2026-09-11 | 1,588 daily files |
| 5 | Minute NIFTY spot | `data/minute/index/NIFTY.parquet` | 2021-05-24 → 2026-07-02 | 486,050 rows |
| 6 | Minute BANKNIFTY spot | `data/minute/index/BANKNIFTY.parquet` | 2021-05-27 → 2026-07-02 | 486,861 rows |
| 7 | Minute NIFTY options (all strikes, per expiry) | `data/minute/options/NIFTY/<expiry>.parquet` | 2021-05-27 → 2026-08-04 | 267 expiry files |
| 8 | Minute BANKNIFTY options (all strikes, per expiry) | `data/minute/options/BANKNIFTY/<expiry>.parquet` | 2021-05-27 → 2026-07-28 | 62 expiry files (fewer is correct — BankNifty weekly options were discontinued by SEBI, 20 Nov 2024; monthly only since) |

Note: item 4 (daily bhavcopy) contains BOTH futures (`FUTIDX`) and options
(`OPTIDX`) for NIFTY and BANKNIFTY in every daily file — confirmed by direct
inspection, not assumed.

---

## What needs minute data vs. daily-only

Only a handful of the 15 Phase 1 strategies need minute-level data —
**Expiry-Day Pinning** and the two **Opening Range Breakout** strategies
(Options and Futures variants), which need the first-30-minute range
computed from minute spot data. Every other strategy runs entirely on
daily data (items 1–4 above).

---

## Provenance (where each source came from)

- **Items 1–3** (daily index spot, VIX): extracted and validated from
  Investing.com historical-data PDF exports, cross-checked against item 4's
  futures settlement prices (basis gap ~15–35 points, consistent with normal
  cost-of-carry — not an error).
- **Item 4** (daily F&O bhavcopy): originally sourced from the public repo
  `SantoshSrinivas79/NSE-FNO-Data-bank`, mirrored into this repo so it does
  not depend on that repo remaining available.
- **Items 5–8** (minute index + options): sourced from the Hugging Face
  dataset `thetrademarkk/india-index-options-1m`, filtered to NIFTY and
  BANKNIFTY only (SENSEX excluded — not used in this project).

All data in this repository is now independent of its original source —
if any upstream source disappears, this repository is unaffected.

---

## Known limitations

- Minute data does not reach back to April 2020 the way daily data does —
  this is why the official backtest window (above) starts May 2021, not
  April 2020.
- BANKNIFTY minute options only go up to monthly-expiry granularity from
  Nov 2024 onward, by regulation — not a data gap.
- Bid-ask spread is not present in any daily source (only in Kite's live
  quotes, not historical). See the Phase 1 roadmap PDF, Appendix A, for the
  empirically-derived spread model used in place of this — and
  `pipeline/d_cost_model.py` for the coded version of that model.

---

## Current status (update this as work progresses)

Section 1 (Build) is COMPLETE — Tasks A, B, C, D all done and signed off.
Data acquisition, the SQLite pipeline (`pipeline/`), and the cost/margin
model (`pipeline/d_cost_model.py`) all exist and are tested. Tasks E-G
(the actual backtest engines) and all 15 strategies themselves are not
yet started. See the roadmap PDF for full detail.

