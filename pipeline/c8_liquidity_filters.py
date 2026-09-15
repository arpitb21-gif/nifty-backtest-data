"""
C8 — Liquidity filters. Adds an `is_liquid` flag to daily_options based on
minimum OI and volume (contracts) thresholds, so strategies can exclude
untradeable strikes.

This is flagged in the roadmap as needing YOUR approval, not just a skim —
so I'm proposing a threshold based on the actual data distribution below,
applying it as a default, but this is the one item in C1-C9 I'd genuinely
like you to sanity-check rather than silently trust.
"""
import sqlite3
import pandas as pd
import numpy as np

conn = sqlite3.connect("market.db")
c = conn.cursor()

# Look at the actual distribution to propose a sensible threshold
df = pd.read_sql("""
    SELECT oi, contracts, ABS(moneyness) as abs_moneyness
    FROM daily_options WHERE oi IS NOT NULL AND contracts IS NOT NULL
""", conn)

print("OI distribution (all rows):")
print(df['oi'].describe(percentiles=[.1, .25, .5, .75, .9]))
print("\nContracts (volume) distribution (all rows):")
print(df['contracts'].describe(percentiles=[.1, .25, .5, .75, .9]))

# Near-the-money (within 5%) vs far OTM (beyond 15%) - do liquidity profiles differ a lot?
near = df[df['abs_moneyness'] < 5]
far = df[df['abs_moneyness'] > 15]
print(f"\nNear-ATM (<5% moneyness) median OI: {near['oi'].median():.0f}, median volume: {near['contracts'].median():.0f}")
print(f"Far-OTM (>15% moneyness) median OI: {far['oi'].median():.0f}, median volume: {far['contracts'].median():.0f}")

# PROPOSED THRESHOLD (flagging for review): OI >= 100 AND contracts (volume) >= 10
# Rationale: low enough to keep far-OTM strikes tradeable for Skew Harvesting
# (which specifically targets thin far-OTM strikes), high enough to exclude
# rows with essentially zero real trading interest (OI/volume near 0).
OI_MIN = 100
VOL_MIN = 10

c.execute("ALTER TABLE daily_options ADD COLUMN is_liquid INTEGER")
c.execute(f"""
    UPDATE daily_options SET is_liquid =
    CASE WHEN oi >= {OI_MIN} AND contracts >= {VOL_MIN} THEN 1 ELSE 0 END
""")
conn.commit()

total = c.execute("SELECT COUNT(*) FROM daily_options").fetchone()[0]
liquid = c.execute("SELECT COUNT(*) FROM daily_options WHERE is_liquid=1").fetchone()[0]
print(f"\nApplied threshold OI>={OI_MIN}, volume>={VOL_MIN}: {liquid}/{total} rows ({liquid/total*100:.1f}%) marked liquid")

# Check impact specifically on far-OTM rows (matters for Skew Harvesting / Iron Condor wings)
far_total = c.execute("SELECT COUNT(*) FROM daily_options WHERE ABS(moneyness) > 10").fetchone()[0]
far_liquid = c.execute("SELECT COUNT(*) FROM daily_options WHERE ABS(moneyness) > 10 AND is_liquid=1").fetchone()[0]
print(f"Far-OTM (>10% moneyness) rows: {far_liquid}/{far_total} ({far_liquid/far_total*100:.1f}%) pass the filter")

conn.close()
