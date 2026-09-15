"""
C5 — Implied volatility, FULLY VECTORIZED (numpy arrays across all rows
simultaneously) via Newton-Raphson. A per-row scipy.brentq loop was tested
first and was too slow at 5.4M rows (would take well over an hour) — this
version does every row's iteration as one array operation, finishing in
under a minute.

Same rate/dividend/skip assumptions as before (see ASSUMPTIONS_LOG.md).
"""
import sqlite3
import numpy as np
from scipy.stats import norm
import time

conn = sqlite3.connect("market.db")
c = conn.cursor()
R = 0.065

t0 = time.time()
rows = c.execute("""
    SELECT rowid, spot_ref, strike, dte, settle, option_type
    FROM daily_options
    WHERE dte > 0 AND settle > 0 AND moneyness IS NOT NULL
""").fetchall()
print(f"Fetched {len(rows)} rows in {time.time()-t0:.1f}s")

rowids = np.array([r[0] for r in rows])
S = np.array([r[1] for r in rows], dtype=np.float64)
K = np.array([r[2] for r in rows], dtype=np.float64)
T = np.array([r[3] for r in rows], dtype=np.float64) / 365.0
price = np.array([r[4] for r in rows], dtype=np.float64)
is_call = np.array([r[5] == 'CE' for r in rows])

intrinsic = np.where(is_call, np.maximum(0, S - K), np.maximum(0, K - S))
has_time_value = price > intrinsic + 1e-6

sigma = np.full(len(rows), 0.20)  # initial guess: 20% IV

for it in range(50):
    d1 = (np.log(S / K) + (R + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    call_price = S * norm.cdf(d1) - K * np.exp(-R * T) * norm.cdf(d2)
    put_price = K * np.exp(-R * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    model_price = np.where(is_call, call_price, put_price)

    vega = S * norm.pdf(d1) * np.sqrt(T)
    vega = np.where(vega < 1e-8, 1e-8, vega)  # avoid divide-by-zero

    diff = model_price - price
    step = diff / vega
    step = np.clip(step, -1.0, 1.0)  # damp huge jumps for stability
    sigma = sigma - step
    sigma = np.clip(sigma, 1e-4, 5.0)

    if it % 10 == 0:
        max_err = np.abs(diff[has_time_value]).max() if has_time_value.any() else 0
        print(f"  iter {it}: max price error = {max_err:.4f}")

# Recompute final price error to judge convergence per-row
d1 = (np.log(S / K) + (R + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
d2 = d1 - sigma * np.sqrt(T)
call_price = S * norm.cdf(d1) - K * np.exp(-R * T) * norm.cdf(d2)
put_price = K * np.exp(-R * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
model_price = np.where(is_call, call_price, put_price)
converged = np.abs(model_price - price) < 0.5  # within 0.5 rupee

final_iv = np.where(has_time_value & converged, sigma, np.nan)

n_valid = (~np.isnan(final_iv)).sum()
print(f"\nConverged IVs: {n_valid} / {len(rows)}")
print(f"Total compute time: {time.time()-t0:.1f}s")

updates = [(float(iv), int(rid)) for iv, rid in zip(final_iv, rowids) if not np.isnan(iv)]
c.executemany("UPDATE daily_options SET iv=? WHERE rowid=?", updates)
conn.commit()

sample = c.execute("""
    SELECT symbol, date, strike, option_type, settle, iv, moneyness
    FROM daily_options WHERE iv IS NOT NULL AND ABS(moneyness) < 1
    ORDER BY date DESC LIMIT 5
""").fetchall()
print("\nSample near-ATM IVs:")
for r in sample:
    print(" ", r)
conn.close()
