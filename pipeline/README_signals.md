# pipeline/ additions — extended signals (daily + minute)

Run AFTER the original C1-C9 pipeline (see the main pipeline/README.md)
and Task D. These scripts add the expanded signal set, both daily and
minute-level, discussed and built in a later session.

## Run order (dependencies matter)

```
python3 pipeline/daily_signals_extended.py      # ~30 sec — needs C7's chain_signals table
python3 pipeline/build_minute_indicators.py     # ~14 sec — MA/RSI/MACD, 4 timeframes, both symbols
python3 pipeline/minute_pcr_oi.py               # ~90 sec — PCR + OI buildup, all 329 expiry files
python3 pipeline/minute_index_signals.py        # ~30 sec — returns/ATR/52w-high/streak/ROC/RV/spread z-score
```

Then the slow one — minute-level ATM IV, skew, max-pain. This takes
**~30-45 minutes total** because it runs a real Newton-Raphson IV solve
plus per-minute max-pain computation across 329 files. If your
environment has a per-command time limit, run it in chunks:

```
python3 pipeline/minute_chain_signals.py 0 329     # all at once, if no time limit
# OR, chunked (adjust range size to your environment's limit):
python3 pipeline/minute_chain_signals.py 0 30
python3 pipeline/minute_chain_signals.py 30 60
# ...continue until you reach 329. Each chunk commits its own progress —
# safe to stop and resume from wherever it left off.
```

Finally, two signals that depend on the chain signals above:

```
python3 pipeline/minute_iv_rv_spread.py         # ~5 sec — needs minute_chain_signals + minute_index_signals
python3 pipeline/minute_rank_signals.py         # ~30 sec — needs minute_chain_signals
```

`minute_signals.py` is a shared library (resampling + RSI/MACD/MA
functions), imported by the others — not run standalone.

## Resolution decisions, logged

- Minute index indicators (MA/RSI/MACD): 5/15/30/60-minute timeframes
- Minute PCR/OI, minute chain signals (ATM IV/skew/max-pain): raw
  1-minute resolution
- **Minute IV rank / skew rank: 5-minute resolution, not 1-minute** —
  exact 1-minute timed out; 5-min runs in ~30s and a rolling percentile
  changes slowly enough that the difference is immaterial. Decided
  explicitly, not a silent shortcut.

## Two things that do NOT exist and cannot be built

- **Minute-level VIX regime** — India VIX has no minute-level
  publication anywhere.
- **Minute-level futures rollover %** — no minute-level futures data
  exists in any of our sources (only index spot and options at minute
  granularity).

Both stay daily-only, correctly, not as a gap to fix later.

## Full signal table (daily/minute possible + done status)

| Signal | Daily done | Minute done | Minute frequency |
|---|---|---|---|
| Returns (1d/5d/12m) | Y | Y | 5-min |
| ATR(14) | Y | Y | 5-min |
| 52-week-high distance | Y | Y | 5-min |
| Nifty-BankNifty spread z-score | Y | Y | 5-min |
| ATM IV | Y | Y | 1-min |
| Skew | Y | Y | 1-min |
| Max-pain | Y | Y | 1-min |
| IV rank / skew rank | Y | Y | 5-min |
| Moving averages (20/50/200) | Y | Y | 5/15/30/60-min |
| RSI(14) | Y | Y | 5/15/30/60-min |
| MACD | Y | Y | 5/15/30/60-min |
| PCR (OI + volume) | Y | Y | 1-min |
| OI buildup/unwind | Y | Y | 1-min |
| ROC | Y | Y | 5-min (3/10-candle only, fewer windows than daily) |
| Streak count | Y | Y | 5-min |
| Gap size | Y | N/A | not applicable at minute level |
| Realized vol | Y | Y | 5-min (20/60-candle only) |
| IV-RV spread | Y | Y | 5-min |
| VIX regime bucket | Y | N — hard wall | no minute VIX exists anywhere |
| Futures rollover % | Y | N — hard wall | no minute futures data exists |
