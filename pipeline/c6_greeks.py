"""
C6 — Greeks (delta, gamma, theta, vega) for every row that has a valid IV
from C5. Standard Black-Scholes closed-form Greeks, vectorized.

Theta is reported per calendar day (annual theta / 365), which is the
convention traders actually use ("how much value does this lose today"),
not the raw per-year value the formula naturally produces.
"""
import sqlite3
import numpy as np
from scipy.stats import norm

conn = sqlite3.connect("market.db")
c = conn.cursor()
R = 0.065

rows = c.execute("""
    SELECT rowid, spot_ref, strike, dte, iv, option_type
    FROM daily_options WHERE iv IS NOT NULL
""").fetchall()
print(f"Computing Greeks for {len(rows)} rows...")

rowids = np.array([r[0] for r in rows])
S = np.array([r[1] for r in rows], dtype=np.float64)
K = np.array([r[2] for r in rows], dtype=np.float64)
T = np.array([r[3] for r in rows], dtype=np.float64) / 365.0
sigma = np.array([r[4] for r in rows], dtype=np.float64)
is_call = np.array([r[5] == 'CE' for r in rows])

d1 = (np.log(S / K) + (R + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
d2 = d1 - sigma * np.sqrt(T)

delta = np.where(is_call, norm.cdf(d1), norm.cdf(d1) - 1)
gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
vega = S * norm.pdf(d1) * np.sqrt(T) / 100  # per 1% change in IV, trader convention

theta_call = (-S * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) - R * K * np.exp(-R * T) * norm.cdf(d2)) / 365
theta_put = (-S * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) + R * K * np.exp(-R * T) * norm.cdf(-d2)) / 365
theta = np.where(is_call, theta_call, theta_put)

updates = list(zip(
    [float(x) for x in delta], [float(x) for x in gamma],
    [float(x) for x in theta], [float(x) for x in vega],
    [int(x) for x in rowids]
))
c.executemany("UPDATE daily_options SET delta=?, gamma=?, theta=?, vega=? WHERE rowid=?", updates)
conn.commit()

sample = c.execute("""
    SELECT symbol, date, strike, option_type, iv, delta, gamma, theta, vega
    FROM daily_options WHERE delta IS NOT NULL AND ABS(moneyness) < 1
    ORDER BY date DESC LIMIT 5
""").fetchall()
print("\nSample near-ATM Greeks (delta should be ~0.5/-0.5 for ATM):")
for r in sample:
    print(" ", r)
conn.close()
