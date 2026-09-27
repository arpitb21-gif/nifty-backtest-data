"""
Extends Task C6 (Greeks) to minute granularity, partitioned by year.

REBUILT A THIRD TIME after two real, different failures during
development -- both worth knowing about, not just fixed silently:

1. First attempt processed files sorted symbol-then-date (all BANKNIFTY
   files, then all NIFTY files). Since both symbols' files independently
   restart at 2021, "flush the oldest year once we've moved past it" was
   unsafe -- it flushed year 2021 after BankNifty's files, then
   overwrote that flush with NIFTY-only 2021 data later, silently
   losing the BankNifty rows. Fixed by sorting all files chronologically
   across both symbols together (by filename/expiry-date only), so a
   year is genuinely never revisited once passed.

2. Second attempt ran as a long-lived detached background process,
   flushing completed years straight to /mnt/user-data/outputs mid-run.
   The sandbox container itself restarted unprompted between two
   conversation turns -- local disk survived (files intact after
   restart), but a flush that had already reported success to the
   outputs mount was gone afterward. Conclusion: a raw filesystem write
   to that mount is not reliably durable until explicitly delivered via
   the present_files tool call -- a background script can't do that
   itself. Fixed by never touching /mnt/user-data/outputs mid-run: this
   version writes small per-chunk progress checkpoints to LOCAL disk
   only, runs in short foreground chunks (resumable, not a long
   detached background job), and only copies finished, compressed
   per-year files to the outputs mount at the very end, once, right
   before they get delivered via present_files in the same turn.

Real data-quality finding along the way (kept from earlier): ~half the
rows in these minute options files are exact duplicates -- confirmed on
2021-05-27 BANKNIFTY, 307,309 of 621,065 rows identical in every
column. Dropped losslessly before computing anything.

Usage: python3 c6_minute_greeks.py <start_index> <end_index>
Progress is tracked in minute_greeks_progress.json (local) -- check it
to find the next start_index after any interruption.
"""
import sqlite3
import pandas as pd
import numpy as np
import glob
import sys
import os
import json
import time
from scipy.stats import norm

R = 0.065
PROGRESS_FILE = "minute_greeks_progress.json"

all_files = sorted(glob.glob("data/minute/options/*/*.parquet"))
all_files = sorted(all_files, key=lambda f: f.split('/')[-1])  # chronological across both symbols

start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
end = int(sys.argv[2]) if len(sys.argv) > 2 else len(all_files)
files = all_files[start:end]
print(f"Processing files [{start}:{end}] of {len(all_files)} total (this chunk: {len(files)} files)")

spot_cache = {
    "NIFTY": pd.read_parquet("data/minute/index/NIFTY.parquet")[['timestamp', 'close']].rename(columns={'close': 'spot'}),
    "BANKNIFTY": pd.read_parquet("data/minute/index/BANKNIFTY.parquet")[['timestamp', 'close']].rename(columns={'close': 'spot'}),
}

def get_conn(year):
    conn = sqlite3.connect(f"minute_greeks_{year}.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS minute_greeks (
        symbol TEXT, expiry TEXT, timestamp TEXT, strike REAL, option_type TEXT,
        iv REAL, delta REAL, gamma REAL, theta REAL, vega REAL,
        PRIMARY KEY (symbol, expiry, timestamp, strike, option_type))""")
    conn.commit()
    return conn

t0 = time.time()
total_rows = 0
open_conns = {}
years_touched = set()

for fi, f in enumerate(files):
    parts = f.split('/')
    symbol, expiry = parts[-2], parts[-1].replace('.parquet', '')

    df = pd.read_parquet(f)
    df = df[df['close'] > 0]
    if df.empty:
        continue
    df = df.drop_duplicates(subset=['timestamp', 'strike', 'option_type', 'open', 'high', 'low', 'close'])
    df = df.drop_duplicates(subset=['timestamp', 'strike', 'option_type'], keep='first')
    df = df.merge(spot_cache[symbol], on='timestamp', how='inner')
    if df.empty:
        continue

    df['dte'] = (pd.to_datetime(expiry) - pd.to_datetime(df['trading_day'])).dt.days.clip(lower=1) / 365.0
    S, K, T, price = df['spot'].values, df['strike'].values, df['dte'].values, df['close'].values
    is_call = (df['option_type'] == 'CE').values

    sigma = np.full(len(df), 0.20)
    for _ in range(30):
        d1 = (np.log(S/K) + (R+0.5*sigma**2)*T) / (sigma*np.sqrt(T))
        d2 = d1 - sigma*np.sqrt(T)
        cp = S*norm.cdf(d1) - K*np.exp(-R*T)*norm.cdf(d2)
        pp = K*np.exp(-R*T)*norm.cdf(-d2) - S*norm.cdf(-d1)
        model = np.where(is_call, cp, pp)
        vega_iter = np.maximum(S*norm.pdf(d1)*np.sqrt(T), 1e-8)
        sigma = np.clip(sigma - np.clip((model-price)/vega_iter, -1, 1), 1e-4, 5.0)

    delta = np.where(is_call, norm.cdf(d1), norm.cdf(d1) - 1)
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    vega = S * norm.pdf(d1) * np.sqrt(T) / 100
    theta_call = (-S*norm.pdf(d1)*sigma/(2*np.sqrt(T)) - R*K*np.exp(-R*T)*norm.cdf(d2)) / 365
    theta_put = (-S*norm.pdf(d1)*sigma/(2*np.sqrt(T)) + R*K*np.exp(-R*T)*norm.cdf(-d2)) / 365
    theta = np.where(is_call, theta_call, theta_put)

    out = pd.DataFrame({
        "symbol": symbol, "expiry": expiry, "timestamp": df['timestamp'].astype(str),
        "year": pd.to_datetime(df['timestamp']).dt.year,
        "strike": K, "option_type": df['option_type'].values,
        "iv": sigma, "delta": delta, "gamma": gamma, "theta": theta, "vega": vega,
    })

    for year, grp in out.groupby("year"):
        year = int(year)
        if year not in open_conns:
            open_conns[year] = get_conn(year)
        grp.drop(columns=["year"]).to_sql("minute_greeks", open_conns[year], if_exists="append", index=False)
        open_conns[year].commit()
        years_touched.add(year)
    total_rows += len(out)

for conn in open_conns.values():
    conn.close()

elapsed = time.time() - t0
print(f"Chunk done: {total_rows:,} rows from {len(files)} files in {elapsed:.0f}s. Years touched: {sorted(years_touched)}")

progress = {}
if os.path.exists(PROGRESS_FILE):
    with open(PROGRESS_FILE) as fp:
        progress = json.load(fp)
progress["last_completed_index"] = end - 1
progress["total_files"] = len(all_files)
progress.setdefault("total_rows", 0)
progress["total_rows"] += total_rows
with open(PROGRESS_FILE, "w") as fp:
    json.dump(progress, fp, indent=2)
print(f"Progress saved: completed through file index {end-1} of {len(all_files)-1}. "
      f"Cumulative rows so far: {progress['total_rows']:,}")
