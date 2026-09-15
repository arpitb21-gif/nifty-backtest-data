"""
C4 — Build a continuous futures series per symbol, rolling to the next
contract at expiry, so strategies can treat "the futures price" as one
unbroken series rather than juggling multiple simultaneous contracts.

ASSUMPTION (logged): roll rule = use the NEAR-MONTH (nearest expiry)
contract every day, and roll to the next contract the day AFTER the
near-month contract expires. This is the standard, simplest convention
(as opposed to volume-based or open-interest-based rolling, which are
more common for commodities than liquid index futures where near-month
dominates volume almost the whole time anyway).

ASSUMPTION (logged): "adjustment" is NOT applied (i.e. this is an
unadjusted continuous series — there will be small jumps at each roll
date equal to the futures basis between contracts). This is deliberate:
our futures-based strategies (breakout, spread reversion, momentum) all
use RETURNS, not price levels, and unadjusted returns spike only on the
roll day itself. A back-adjusted series would be needed if we cared about
absolute price levels over time, which none of our 7 futures tasks do.
"""
import sqlite3

conn = sqlite3.connect("market.db")
c = conn.cursor()

c.execute("DROP TABLE IF EXISTS continuous_futures")
c.execute("""
CREATE TABLE continuous_futures (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    expiry TEXT NOT NULL,     -- which contract this day's price came from
    close  REAL,
    settle REAL,
    is_roll_day INTEGER,      -- 1 if this is the first day of a new contract
    PRIMARY KEY (symbol, date)
)
""")

for symbol in ("NIFTY", "BANKNIFTY"):
    rows = c.execute("""
        SELECT date, expiry, close, settle FROM daily_futures
        WHERE symbol = ? ORDER BY date, expiry
    """, (symbol,)).fetchall()

    # Group by date, pick the nearest (smallest) expiry >= date for each day
    from collections import defaultdict
    by_date = defaultdict(list)
    for date, expiry, close, settle in rows:
        by_date[date].append((expiry, close, settle))

    prev_expiry = None
    out = []
    for date in sorted(by_date.keys()):
        contracts = sorted(by_date[date], key=lambda x: x[0])  # sort by expiry asc
        # near-month = smallest expiry still >= date is naturally first after sort
        # (expired contracts for this date shouldn't exist in bhavcopy, but guard anyway)
        valid = [x for x in contracts if x[0] >= date]
        if not valid:
            continue
        expiry, close, settle = valid[0]
        is_roll = 1 if (prev_expiry is not None and expiry != prev_expiry) else 0
        out.append((symbol, date, expiry, close, settle, is_roll))
        prev_expiry = expiry

    c.executemany("INSERT INTO continuous_futures VALUES (?,?,?,?,?,?)", out)
    n_rolls = sum(r[5] for r in out)
    print(f"{symbol}: {len(out)} days, {n_rolls} roll events")

conn.commit()

# Sanity: check a roll date manually
sample = c.execute("""
    SELECT * FROM continuous_futures WHERE symbol='NIFTY' AND is_roll_day=1
    ORDER BY date LIMIT 5
""").fetchall()
print("\nSample roll days (NIFTY):")
for r in sample:
    print(" ", r)
conn.close()
