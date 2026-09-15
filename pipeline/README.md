# pipeline/ — Task C data pipeline

Run these in order, from the repo root, to build `market.db` (a SQLite
database) from the raw data already in `data/`. Full rebuild takes
~8 minutes (tested end-to-end from a fresh clone).

```
pip install scipy --break-system-packages   # one-time, for c5
python3 pipeline/c1_schema.py               # <1 sec  — creates market.db, 8 tables
python3 pipeline/c2a_load_spot_vix.py       # <5 sec  — loads daily spot + VIX CSVs
python3 pipeline/c2b_load_bhavcopy.py       # ~4 min  — loads all 1,588 daily F&O bhavcopy files
python3 pipeline/c3_reconstruct_chains.py   # ~1 min  — adds moneyness + DTE to every option row
python3 pipeline/c4_continuous_futures.py   # ~1 min  — builds rolled continuous futures series
python3 pipeline/c5_iv_vectorized.py        # ~90 sec — implied volatility (vectorized Newton-Raphson)
python3 pipeline/c6_greeks.py               # ~30 sec — delta, gamma, theta, vega
python3 pipeline/c7_derived_signals.py      # ~2 min  — IV rank, skew, max-pain, ATR, spread z-score
python3 pipeline/c8_liquidity_filters.py    # <5 sec  — flags is_liquid (OI>=100, volume>=10)
python3 pipeline/c9_validation.py           # <5 sec  — full data quality report
```

**Not included:** `c5_implied_vol.py` — an earlier, much slower per-row
approach (scipy.brentq in a Python loop), superseded by
`c5_iv_vectorized.py`. Don't build it back in; the vectorized version
is the one to use.

**Assumptions and decisions made while building this pipeline** are in
`ASSUMPTIONS_LOG.md` in this same folder — read that before changing
any of these scripts, since several non-obvious choices (risk-free
rate, liquidity threshold, skew proxy definition, etc.) are explained
there with the reasoning behind them.

**Output:** a single file, `market.db`, created at the repo root.
Not committed to the repo itself (1.3GB, a derived artifact — see
the main README.md's "Context for a New AI Session" section for why).
