"""
Opening Range Breakout (ORB) signal computation.

Builds the first-30-minute (09:15-09:45 IST) high/low range per trading
day, for NIFTY and BANKNIFTY, from minute-level index spot data. This is
prep work specific to the two ORB strategies (Futures and Options) added
to Sections 2/3 of the roadmap - NOT part of the core Task C sequence,
which is already complete and signed off. Task C deliberately left
minute-level data unprocessed except where a specific strategy needs it
(logged in C1) - this is that trigger, for these two strategies only.

ASSUMPTION (logged): opening range = 09:15:00 to 09:44:59 IST inclusive
(the first 30 one-minute bars of the trading session). Any row before
09:15 (seen in the raw data, e.g. 09:07) is pre-market and excluded.

Output: a table (orb_signals) with, per (symbol, date): or_high, or_low,
or_range (high-low), and the breakout direction/time if one occurred
before market close that day (14:30 cutoff to allow the ORB strategies
a realistic decision-and-exit window before 15:30 close - not literally
15:29, since a breakout with 1 minute left to trade isn't tradeable).
"""
import sqlite3
import pandas as pd

conn = sqlite3.connect("market.db")

for symbol in ("NIFTY", "BANKNIFTY"):
    df = pd.read_parquet(f"data/minute/index/{symbol}.parquet")
    df = df.sort_values("timestamp")

    results = []
    for day, g in df.groupby("trading_day"):
        g = g.set_index("timestamp")
        # Opening range: 09:15 to 09:44 inclusive (first 30 one-minute bars)
        or_window = g.between_time("09:15", "09:44")
        if or_window.empty:
            continue
        or_high = or_window["high"].max()
        or_low = or_window["low"].min()

        # Look for the first breakout after 09:45, up to 14:30 (leaves a
        # realistic window to act on it before the 15:30 close)
        after = g.between_time("09:45", "14:30")
        breakout_time, breakout_dir = None, None
        for ts, row in after.iterrows():
            if row["high"] > or_high:
                breakout_time, breakout_dir = ts.strftime("%H:%M"), "UP"
                break
            if row["low"] < or_low:
                breakout_time, breakout_dir = ts.strftime("%H:%M"), "DOWN"
                break

        results.append((symbol, day, or_high, or_low, or_high - or_low,
                         breakout_dir, breakout_time))

    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS orb_signals (
        symbol TEXT, date TEXT, or_high REAL, or_low REAL, or_range REAL,
        breakout_dir TEXT, breakout_time TEXT, PRIMARY KEY (symbol, date))""")
    c.executemany("INSERT OR REPLACE INTO orb_signals VALUES (?,?,?,?,?,?,?)", results)
    conn.commit()

    n_days = len(results)
    n_breakouts = sum(1 for r in results if r[5] is not None)
    n_up = sum(1 for r in results if r[5] == "UP")
    n_down = sum(1 for r in results if r[5] == "DOWN")
    print(f"{symbol}: {n_days} days, {n_breakouts} had a breakout "
          f"({n_up} up, {n_down} down, {n_days-n_breakouts} no breakout / range held)")

# Sanity check
c = conn.cursor()
sample = c.execute("SELECT * FROM orb_signals ORDER BY date DESC LIMIT 5").fetchall()
print("\nSample rows:")
for r in sample:
    print(" ", r)
conn.close()
