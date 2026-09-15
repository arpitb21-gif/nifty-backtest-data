"""
C3 — Reconstruct daily options chains: add moneyness (%) and days-to-expiry
per row, using daily_spot as the underlying reference price.

ASSUMPTION (logged): "moneyness" = (strike - spot) / spot * 100 for calls,
and (spot - strike) / spot * 100 for puts is NOT what we store — instead we
store a single signed moneyness = (strike - spot) / spot * 100 for ALL rows
(same sign convention regardless of CE/PE), since that's simpler to filter
on later ("strikes within 5% of spot") and the strategies care about
distance-from-spot, not moneyness-in-the-option-pricing-sense.

ASSUMPTION (logged): days-to-expiry is calendar days, not trading days.
Trading-day DTE would be more precise for theta decay but calendar-day is
what's conventionally used for "weekly = ~7 DTE" filters, and matches how
the strategy specs in the roadmap describe entry timing.
"""
import sqlite3
from datetime import datetime

conn = sqlite3.connect("market.db")
c = conn.cursor()

# Add columns (SQLite: skip if already present)
for col, typ in [("moneyness", "REAL"), ("dte", "INTEGER"), ("spot_ref", "REAL")]:
    try:
        c.execute(f"ALTER TABLE daily_options ADD COLUMN {col} {typ}")
    except sqlite3.OperationalError:
        pass  # already exists

# Build a fast lookup: (symbol, date) -> spot close
spot_map = {}
for symbol, date, close in c.execute("SELECT symbol, date, close FROM daily_spot"):
    spot_map[(symbol, date)] = close

rows = c.execute("SELECT rowid, symbol, date, expiry, strike FROM daily_options").fetchall()
print(f"Processing {len(rows)} option rows...")

updates = []
missing_spot = 0
for rowid, symbol, date, expiry, strike in rows:
    spot = spot_map.get((symbol, date))
    if spot is None:
        missing_spot += 1
        continue
    moneyness = (strike - spot) / spot * 100
    d1 = datetime.strptime(date, "%Y-%m-%d")
    d2 = datetime.strptime(expiry, "%Y-%m-%d")
    dte = (d2 - d1).days
    updates.append((moneyness, dte, spot, rowid))

print(f"Rows with no matching spot price: {missing_spot}")

c.executemany("UPDATE daily_options SET moneyness=?, dte=?, spot_ref=? WHERE rowid=?", updates)
conn.commit()

# Sanity check
sample = c.execute("""
    SELECT symbol, date, expiry, strike, option_type, moneyness, dte, spot_ref
    FROM daily_options WHERE dte >= 0 ORDER BY date DESC LIMIT 5
""").fetchall()
print("\nSample rows:")
for r in sample:
    print(" ", r)

neg_dte = c.execute("SELECT COUNT(*) FROM daily_options WHERE dte < 0").fetchone()[0]
print(f"\nRows with negative DTE (expiry before trade date - should be 0 or explainable): {neg_dte}")
conn.close()
