"""
Precomputed minute-level indicators, multiple timeframes, both indices.

REVISED DECISION (logged): originally built as an on-demand-only toolkit
(minute_signals.py), reasoning that precomputing was wasteful. Checked
actual row counts before deciding further: even the densest resampling
(5-min candles) is only ~99,000 rows per symbol across the full 2021-2026
history - trivially cheap, not the 750M-row options-chain scale that
WOULD be genuinely expensive to precompute exhaustively. Corrected:
precompute these now, for real, across a sensible set of timeframes.

Scope, deliberately: this covers INDEX-LEVEL price indicators (RSI, MACD,
moving averages) at 5/15/30/60-minute timeframes, for NIFTY and BANKNIFTY
spot. It does NOT precompute per-strike options-chain signals (PCR, OI
buildup) at minute granularity across all strikes - that IS the
750M-row-scale dataset, genuinely large, and still built on-demand only
(via minute_signals.py) if a specific future strategy needs it.
"""
import sqlite3
import pandas as pd
import time
from minute_signals import resample_ohlc, rsi, macd, moving_average

t0 = time.time()
conn = sqlite3.connect("market.db")
c = conn.cursor()

c.execute("DROP TABLE IF EXISTS minute_indicators")
c.execute("""CREATE TABLE minute_indicators (
    symbol TEXT, timeframe TEXT, timestamp TEXT,
    close REAL, rsi_14 REAL, macd REAL, macd_signal REAL,
    ma_20 REAL, ma_50 REAL,
    PRIMARY KEY (symbol, timeframe, timestamp)
)""")

TIMEFRAMES = ['5min', '15min', '30min', '60min']
total_rows = 0

for symbol in ("NIFTY", "BANKNIFTY"):
    df = pd.read_parquet(f"data/minute/index/{symbol}.parquet")
    df = df.sort_values('timestamp')

    for tf in TIMEFRAMES:
        resampled = resample_ohlc(df, tf)
        resampled['rsi_14'] = rsi(resampled['close'], 14)
        macd_line, macd_sig, _ = macd(resampled['close'])
        resampled['macd'] = macd_line
        resampled['macd_signal'] = macd_sig
        resampled['ma_20'] = moving_average(resampled['close'], 20)
        resampled['ma_50'] = moving_average(resampled['close'], 50)

        rows = [(symbol, tf, r['timestamp'].isoformat(), r['close'],
                 r['rsi_14'] if pd.notna(r['rsi_14']) else None,
                 r['macd'] if pd.notna(r['macd']) else None,
                 r['macd_signal'] if pd.notna(r['macd_signal']) else None,
                 r['ma_20'] if pd.notna(r['ma_20']) else None,
                 r['ma_50'] if pd.notna(r['ma_50']) else None)
                for _, r in resampled.iterrows()]
        c.executemany("INSERT OR REPLACE INTO minute_indicators VALUES (?,?,?,?,?,?,?,?,?)", rows)
        total_rows += len(rows)
        print(f"{symbol} {tf}: {len(rows)} candles")

conn.commit()
print(f"\nTotal rows written: {total_rows}")
print(f"Build time: {time.time()-t0:.1f}s")

sample = c.execute("""
    SELECT symbol, timeframe, timestamp, close, rsi_14, ma_20
    FROM minute_indicators WHERE rsi_14 IS NOT NULL
    ORDER BY timestamp DESC LIMIT 5
""").fetchall()
print("\nSample rows:")
for r in sample:
    print(" ", r)
conn.close()
