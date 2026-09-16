"""
Minute-level index-based signals: returns, ATR, 52-week-high distance,
streak count, ROC, realized vol, Nifty-BankNifty spread z-score.

All computed on 5-minute candles (not raw 1-minute) - reasonable balance
between granularity and noise for these particular signal types, and
matches the resampling approach already validated in minute_signals.py.
Windows are expressed in 5-min-candle counts, not calendar time.
"""
import sqlite3
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, '.')
from minute_signals import resample_ohlc

conn = sqlite3.connect("market.db")
c = conn.cursor()

# ~78 five-min candles/trading day (09:15-15:30) -> 252 days = ~19,656; use that for 52w-high
CANDLES_PER_DAY = 78
WINDOW_52W = CANDLES_PER_DAY * 252

all_rows = {}
for symbol in ("NIFTY", "BANKNIFTY"):
    df = pd.read_parquet(f"data/minute/index/{symbol}.parquet")
    r5 = resample_ohlc(df, '5min').sort_values('timestamp').reset_index(drop=True)

    ret = r5['close'].pct_change()
    r5['ret_1c'] = ret
    r5['ret_5c'] = r5['close'].pct_change(5)
    r5['ret_20c'] = r5['close'].pct_change(20)

    tr = pd.concat([
        r5['high'] - r5['low'],
        (r5['high'] - r5['close'].shift()).abs(),
        (r5['low'] - r5['close'].shift()).abs()
    ], axis=1).max(axis=1)
    r5['atr_14c'] = tr.rolling(14, min_periods=5).mean()

    r5['high_52w'] = r5['close'].rolling(WINDOW_52W, min_periods=CANDLES_PER_DAY*30).max()
    r5['dist_from_52w_high'] = (r5['close'] - r5['high_52w']) / r5['high_52w'] * 100

    direction = np.sign(ret)
    streak = direction.groupby((direction != direction.shift()).cumsum()).cumcount() + 1
    r5['streak'] = streak * direction.fillna(0)

    r5['roc_3c'] = r5['close'].pct_change(3) * 100
    r5['roc_10c'] = r5['close'].pct_change(10) * 100

    r5['rv_20c'] = ret.rolling(20).std() * np.sqrt(252 * CANDLES_PER_DAY) * 100
    r5['rv_60c'] = ret.rolling(60).std() * np.sqrt(252 * CANDLES_PER_DAY) * 100

    all_rows[symbol] = r5

c.execute("DROP TABLE IF EXISTS minute_signals_index")
c.execute("""CREATE TABLE minute_signals_index (
    symbol TEXT, timestamp TEXT, close REAL,
    ret_1c REAL, ret_5c REAL, ret_20c REAL, atr_14c REAL,
    dist_from_52w_high REAL, streak REAL, roc_3c REAL, roc_10c REAL,
    rv_20c REAL, rv_60c REAL, PRIMARY KEY (symbol, timestamp))""")

total = 0
for symbol, r5 in all_rows.items():
    rows = [(symbol, r['timestamp'].isoformat(), r['close'], r['ret_1c'], r['ret_5c'], r['ret_20c'],
             r['atr_14c'], r['dist_from_52w_high'], r['streak'], r['roc_3c'], r['roc_10c'],
             r['rv_20c'], r['rv_60c']) for _, r in r5.iterrows()]
    rows = [tuple(None if pd.isna(x) else x for x in row) for row in rows]
    c.executemany("INSERT OR REPLACE INTO minute_signals_index VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    total += len(rows)
    print(f"{symbol}: {len(rows)} rows")

conn.commit()
print(f"Total: {total}")

# ---------- Nifty-BankNifty spread z-score, minute (5-min candles) ----------
n = all_rows['NIFTY'][['timestamp', 'close']].rename(columns={'close': 'nifty'})
b = all_rows['BANKNIFTY'][['timestamp', 'close']].rename(columns={'close': 'banknifty'})
merged = n.merge(b, on='timestamp', how='inner').sort_values('timestamp')
merged['ratio'] = merged['banknifty'] / merged['nifty']
merged['zscore_60c'] = (merged['ratio'] - merged['ratio'].rolling(60).mean()) / merged['ratio'].rolling(60).std()

c.execute("DROP TABLE IF EXISTS minute_spread_signals")
c.execute("""CREATE TABLE minute_spread_signals (timestamp TEXT PRIMARY KEY, ratio REAL, zscore_60c REAL)""")
c.executemany("INSERT OR REPLACE INTO minute_spread_signals VALUES (?,?,?)",
              [(r['timestamp'].isoformat(), r['ratio'], r['zscore_60c'])
               for _, r in merged.iterrows() if pd.notna(r['zscore_60c'])])
conn.commit()
print(f"minute_spread_signals: {merged['zscore_60c'].notna().sum()} rows")
conn.close()
