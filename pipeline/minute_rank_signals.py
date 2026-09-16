"""
Minute-level IV rank / skew rank: rolling percentile rank against a
trailing 252-day window.

RESOLUTION DECISION (logged): built at 5-minute resolution (every 5th
raw observation), not literal 1-minute. Tested both: exact 1-minute
timed out (>280s, would need the same multi-chunk process as
minute_chain_signals.py, ~25-30 min total). 5-minute resolution runs in
~30s combined for both symbols. A rank/percentile changes slowly by
nature (it's ranking against a 252-day window), so the practical
difference between 1-min and 5-min exactness is minimal for any real
strategy trigger. Decided with the user rather than silently chosen.
"""
import sqlite3
import pandas as pd

conn = sqlite3.connect("market.db")
c = conn.cursor()

df = pd.read_sql("SELECT symbol, timestamp, atm_iv, skew FROM minute_chain_signals", conn)
df['timestamp'] = pd.to_datetime(df['timestamp'])

c.execute("DROP TABLE IF EXISTS minute_rank_signals")
c.execute("""CREATE TABLE minute_rank_signals (
    symbol TEXT, timestamp TEXT, iv_rank_252 REAL, skew_rank_252 REAL,
    PRIMARY KEY (symbol, timestamp))""")

total = 0
for symbol, g in df.groupby('symbol'):
    g = g.set_index('timestamp').sort_index()
    iv_sub = g['atm_iv'].dropna().iloc[::5]
    skew_sub = g['skew'].dropna().iloc[::5]

    iv_rank = iv_sub.rolling('252D', min_periods=50).apply(lambda x: (x.iloc[-1] > x).mean()*100, raw=False)
    skew_rank = skew_sub.rolling('252D', min_periods=50).apply(lambda x: (x.iloc[-1] > x).mean()*100, raw=False)

    combined = pd.DataFrame({'iv_rank_252': iv_rank}).join(pd.DataFrame({'skew_rank_252': skew_rank}), how='outer')
    combined = combined.reset_index()
    rows = [(symbol, r['timestamp'].isoformat(),
             r['iv_rank_252'] if pd.notna(r['iv_rank_252']) else None,
             r['skew_rank_252'] if pd.notna(r['skew_rank_252']) else None)
            for _, r in combined.iterrows() if pd.notna(r['iv_rank_252']) or pd.notna(r['skew_rank_252'])]
    c.executemany("INSERT OR REPLACE INTO minute_rank_signals VALUES (?,?,?,?)", rows)
    total += len(rows)
    print(f"{symbol}: {len(rows)} rows")

conn.commit()
print(f"Total: {total}")
conn.close()
