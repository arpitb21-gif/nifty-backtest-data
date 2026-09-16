"""
Minute-level IV-RV spread. Combines minute_chain_signals.atm_iv (raw
1-minute granularity) with minute_signals_index.rv_20c (5-min candle
granularity, already built) - joined by rounding each 1-minute timestamp
down to its containing 5-minute candle.
"""
import sqlite3
import pandas as pd

conn = sqlite3.connect("market.db")

chain = pd.read_sql("""
    SELECT symbol, expiry, timestamp, atm_iv FROM minute_chain_signals
    WHERE atm_iv IS NOT NULL
""", conn)
chain['timestamp'] = pd.to_datetime(chain['timestamp'])
chain['candle_5min'] = chain['timestamp'].dt.floor('5min')

rv = pd.read_sql("SELECT symbol, timestamp, rv_20c FROM minute_signals_index WHERE rv_20c IS NOT NULL", conn)
rv['timestamp'] = pd.to_datetime(rv['timestamp'])

merged = chain.merge(rv, left_on=['symbol', 'candle_5min'], right_on=['symbol', 'timestamp'],
                      suffixes=('', '_candle'), how='inner')
merged['iv_minus_rv'] = merged['atm_iv'] * 100 - merged['rv_20c']

c = conn.cursor()
c.execute("DROP TABLE IF EXISTS minute_iv_rv_spread")
c.execute("""CREATE TABLE minute_iv_rv_spread (
    symbol TEXT, expiry TEXT, timestamp TEXT, iv_minus_rv REAL,
    PRIMARY KEY (symbol, expiry, timestamp))""")
c.executemany("INSERT OR REPLACE INTO minute_iv_rv_spread VALUES (?,?,?,?)",
              [(r['symbol'], r['expiry'], r['timestamp'].isoformat(), r['iv_minus_rv'])
               for _, r in merged.iterrows() if pd.notna(r['iv_minus_rv'])])
conn.commit()
print(f"minute_iv_rv_spread: {len(merged)} rows")
conn.close()
