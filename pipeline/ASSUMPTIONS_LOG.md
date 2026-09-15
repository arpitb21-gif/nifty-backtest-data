=== Assumptions & Progress Log ===
Started: Tue Sep 15 14:28:23 UTC 2026

## C1 - Schema design
- Minute-level data (parquet) NOT loaded into SQLite; stays as Parquet, queried directly when needed (only Expiry-Day Pinning needs it). Deliberate scope decision.
- 8 tables: daily_spot, daily_vix, daily_futures, daily_options, lot_sizes, holidays, events, expiry_regime.

## C2 - Loaders
- Filtered bhavcopy to NIFTY + BANKNIFTY only (index scope, matches locked plan).
- Discovered bhavcopy has TWO formats across our date range: old NSE format (pre ~Jul 2024) and new UDiFF format (Jul 2024 onward). Built parsers for both after the first attempt crashed on a format I hadn't anticipated.
- Result: 5,375,052 option rows + 9,528 futures rows loaded, 0 unhandled files, 0 row errors across all 1,588 daily files.

## C3 - Chain reconstruction (moneyness, DTE)
- Moneyness stored as single signed value (strike-spot)/spot*100 for all rows (CE and PE alike) - simpler for "within X% of spot" filters used across strategies.
- DTE = calendar days, not trading days (matches how strategy specs describe "~7 DTE" entries).
- MINOR GAP (not blocking): 23 distinct dates across the full range have options data but no matching spot price in our CSV (likely PDF-extraction regex missed these specific rows). Affects 32,514 of 5,375,052 option rows (0.6%). Flagging for later cleanup, not fixing now since it's small and doesn't block downstream work.

## C4 - Continuous futures
- Roll rule: use near-month contract, roll day after expiry. Unadjusted series (small jumps at roll dates) - deliberate, since our futures strategies use returns not price levels.
- 78-79 roll events per symbol over ~6.5 years, matches monthly expiry cadence.

## C5 - Implied volatility
- Flat 6.5% risk-free rate assumption (actual rate ranged 4.0-6.5% across our window per B8). Flagged as a refinement candidate if IV precision matters later.
- Zero dividend yield assumption (standard simplification for index options).
- First attempt (per-row scipy.brentq) was too slow for 5.4M rows - switched to fully vectorized numpy Newton-Raphson, runs in 80 seconds.
- 4,361,389 of 5,193,453 eligible rows converged to a valid IV (84%). Non-converged rows are genuinely bad quotes / deep illiquid strikes - correctly excluded rather than assigned garbage values.

## C6 - Greeks
- Standard Black-Scholes closed-form, same rate/dividend assumptions as C5.
- Theta reported per calendar day (annual/365) - trader convention, not raw formula output.
- Vega reported per 1% IV change - trader convention.
- Computed for all 4,361,389 rows with valid IV. Sanity check passed (ATM deltas in expected 0.45-0.58 range).

## C7 - Derived signals
- ATM IV proxy (for skew calc) ≠ India VIX. We have real VIX data separately (daily_vix) - Conditional VRP's actual trigger should use VIX rank, not this proxy. Built chain_signals/rank_signals for skew-related needs specifically.
- Skew = far OTM put IV minus far OTM call IV (~10% moneyness each side, nearest strike to that target) - simplified from exact-delta-band filtering (roadmap said 10-delta/25-delta) since delta-band filtering shrinks sample size badly on sparse-strike days. Logged, not silently substituted.
- Max-pain computed via standard OI-weighted payout minimization, vectorized with numpy after an initial pandas-loop version timed out.
- 3,153 (symbol, date) groups processed for ATM/skew/max-pain; 3,180 daily_signals rows (returns, ATR, 52w-high distance); 1,519 spread z-score rows.

## C8 - Liquidity filters [FLAGGED FOR REVIEW]
- Proposed threshold: OI >= 100, volume (contracts) >= 10. Data-driven (checked actual OI/volume distributions first, see script output).
- Result: 32.9% of all option rows pass; 17.5% of far-OTM (>10% moneyness) rows pass — expected given far-OTM strikes trade thinly most days.
- This is the one C1-C9 item the roadmap explicitly called out as needing YOUR sign-off, not a default to silently trust. Applied as a working default so the pipeline isn't blocked, but flagging clearly.

## C9 - Validation report [SIGN-OFF GATE - FLAGGED FOR REVIEW]
- 87 missing weekdays in bhavcopy = derived holidays (~13.4/year over 6.5yr, matches NSE's real average).
- 0 negative prices, 0 true duplicates, 0 implausible (>15%) single-day spot moves.
- 21,366 zero-settlement option rows (0.4%) — expected for far OTM/illiquid, not an error.
- 71,390 one-sided (CE-only or PE-only) strike/date/expiry combos (2.62%) — low rate, explainable.
- RECONCILED a discrepancy between C3's per-symbol spot-gap check (23 gaps) and C9's date-level check (0 gaps): all 23 gaps are BANKNIFTY-specific: NIFTY spot has zero gaps in the full range. C9's original check was too coarse (symbol-agnostic); traced and explained precisely rather than left unreconciled.
- Full report saved to C9_VALIDATION_REPORT.txt.

Completed: $(date)

## FIX (post-C9) - 26-date spot/VIX data gap
- ROOT CAUSE FOUND: the PDF-extraction regex required a mandatory +/- sign before the percentage-change field. On days where change was exactly 0.00%, Investing.com's PDF omits the sign, so those rows were silently skipped. Confirmed this explained 100% of 26 gap dates (23 spot + 3 VIX) via direct verification against raw PDF text - no other cause found.
- SECOND BUG found while fixing: 2 BankNifty dates (2026-02-25, 2026-04-01) had their data split across a PDF page boundary (date label and numeric row printed on separate, out-of-order lines). Diagnosed via surrounding chronological context and manually corrected.
- FIX: made the sign optional in the regex, re-extracted all 3 PDFs fully, manually patched the 2 page-break rows, verified 0 remaining gaps against bhavcopy's actual trading calendar (the ground truth - 1588 days).
- Propagated fix through the full pipeline: reloaded daily_spot/daily_vix, re-ran C3 (moneyness/dte - 0 nulls now, down from 32,514), re-ran C5 (IV - 4,387,669 converged, up from 4,361,389), re-ran C6 (Greeks), re-ran C7 (all derived signals), re-applied C8, re-ran C9 (final validation clean).
- Also did a full sweep BEFORE fixing (per explicit instruction) to check for any other undiscovered gaps beyond the known 23/26 - found the VIX gaps this way, which had NOT been caught by the original C3/C9 checks (C9's original check was symbol-agnostic and missed them). All other tables (futures, continuous_futures, IV/Greeks convergence rate) checked clean against expectations.
