"""
Minute-level options signals: PCR (by OI and by volume) and OI change,
aggregated across ALL strikes for a symbol, per minute, per expiry.

Confirmed feasible before building: ~53s for the full 145M-row minute
options dataset (329 expiry files), tested directly, not estimated.
"""
import sqlite3
import pandas as pd
import glob
import time

t0 = time.time()
conn = sqlite3.connect("market.db")
c = conn.cursor()

c.execute("DROP TABLE IF EXISTS minute_pcr_oi")
c.execute("""CREATE TABLE minute_pcr_oi (
    symbol TEXT, expiry TEXT, timestamp TEXT,
    pcr_volume REAL, pcr_oi REAL, oi_change_ce REAL, oi_change_pe REAL,
    PRIMARY KEY (symbol, expiry, timestamp))""")

files = sorted(glob.glob("data/minute/options/*/*.parquet"))
print(f"Processing {len(files)} expiry files...")

total_rows = 0
for i, f in enumerate(files):
    parts = f.split('/')
    symbol, expiry = parts[-2], parts[-1].replace('.parquet', '')

    df = pd.read_parquet(f, columns=['timestamp', 'option_type', 'volume', 'open_interest'])
    per_min = df.groupby(['timestamp', 'option_type']).agg({'volume': 'sum', 'open_interest': 'sum'}).unstack()
    per_min.columns = ['_'.join(col) for col in per_min.columns]

    for col in ['volume_CE', 'volume_PE', 'open_interest_CE', 'open_interest_PE']:
        if col not in per_min.columns:
            per_min[col] = 0

    per_min['pcr_volume'] = per_min['volume_PE'] / per_min['volume_CE'].replace(0, pd.NA)
    per_min['pcr_oi'] = per_min['open_interest_PE'] / per_min['open_interest_CE'].replace(0, pd.NA)
    per_min['oi_change_ce'] = per_min['open_interest_CE'].diff()
    per_min['oi_change_pe'] = per_min['open_interest_PE'].diff()
    per_min = per_min.reset_index()

    rows = [(symbol, expiry, r['timestamp'].isoformat(),
             r['pcr_volume'] if pd.notna(r['pcr_volume']) else None,
             r['pcr_oi'] if pd.notna(r['pcr_oi']) else None,
             r['oi_change_ce'] if pd.notna(r['oi_change_ce']) else None,
             r['oi_change_pe'] if pd.notna(r['oi_change_pe']) else None)
            for _, r in per_min.iterrows()]
    c.executemany("INSERT OR REPLACE INTO minute_pcr_oi VALUES (?,?,?,?,?,?,?)", rows)
    total_rows += len(rows)

    if (i + 1) % 50 == 0:
        conn.commit()
        print(f"  ...{i+1}/{len(files)} files done")

conn.commit()
print(f"\nTotal rows: {total_rows:,}")
print(f"Build time: {time.time()-t0:.1f}s")

sample = c.execute("""
    SELECT symbol, expiry, timestamp, pcr_volume, pcr_oi
    FROM minute_pcr_oi WHERE pcr_volume IS NOT NULL
    ORDER BY timestamp DESC LIMIT 5
""").fetchall()
print("\nSample rows:")
for r in sample:
    print(" ", r)
conn.close()
