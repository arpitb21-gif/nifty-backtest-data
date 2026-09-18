"""
Nearest-strike liquidity investigation (C8 supporting evidence).

This query previously only ever existed as an ad-hoc, uncommitted query run
during development. A later audit correctly flagged this: the roadmap
stated a specific finding (99.8% nearest-strike liquidity, 12 explained
exceptions) with no reproducible artifact behind it. This script fixes that
- it is the exact, permanent, re-runnable version of that investigation.

Requires c8_liquidity_filters.py to have been run first (needs the
is_liquid column to exist).

Finds: for the single strike closest to spot, on the single nearest real
expiry (DTE >= 3), per day, per symbol, per option type - what fraction
pass the liquidity filter? This is different from (and much higher than)
the blended average across ALL strikes/expiries, which is what a naive
query would show and why an earlier, less precise check reported a
misleadingly low 32.9%.
"""
import sqlite3

conn = sqlite3.connect("market.db")
c = conn.cursor()

QUERY = """
WITH nearest_per_day AS (
    SELECT symbol, date, MIN(dte) as min_dte
    FROM daily_options WHERE dte >= 3
    GROUP BY symbol, date
),
closest_strike AS (
    SELECT o.symbol, o.date, o.expiry, o.strike, o.option_type, o.moneyness,
           o.oi, o.contracts, o.is_liquid,
           ROW_NUMBER() OVER (PARTITION BY o.symbol, o.date, o.option_type ORDER BY ABS(o.moneyness)) as rn
    FROM daily_options o
    JOIN nearest_per_day n ON o.symbol=n.symbol AND o.date=n.date AND o.dte=n.min_dte
)
"""

print("=== Nearest-strike liquidity rate ===")
summary = c.execute(QUERY + "SELECT is_liquid, COUNT(*) FROM closest_strike WHERE rn=1 GROUP BY is_liquid").fetchall()
total = sum(n for _, n in summary)
liquid = sum(n for liq, n in summary if liq == 1)
print(f"Liquid: {liquid}, Not liquid: {total-liquid}, Total: {total}, Rate: {liquid/total*100:.1f}%")

print("\n=== The exceptions (not liquid) ===")
exceptions = c.execute(QUERY + """
    SELECT symbol, date, expiry, strike, option_type, moneyness, oi, contracts
    FROM closest_strike WHERE rn=1 AND is_liquid=0 ORDER BY date
""").fetchall()
for row in exceptions:
    print(" ", row)

conn.close()
