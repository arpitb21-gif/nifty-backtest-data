"""
Minute-level ATM IV, skew, and max-pain - across all 329 expiry files.
VECTORIZED version - the first attempt used a per-minute Python loop
(groupby + iterate), which was too slow on files with thousands of
unique minutes (a Bank Nifty monthly file alone had 3,384). Rewritten
to use groupby().idxmin() (vectorized in pandas) for ATM/skew selection;
only max-pain still loops per-minute, since it's a genuine per-timestamp
matrix operation, but that part alone is fast.
"""
import sqlite3
import pandas as pd
import numpy as np
import glob
import sys
import time
from scipy.stats import norm

t0 = time.time()
conn = sqlite3.connect("market.db")
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS minute_chain_signals (
    symbol TEXT, expiry TEXT, timestamp TEXT, atm_iv REAL, skew REAL, max_pain_strike REAL,
    PRIMARY KEY (symbol, expiry, timestamp))""")

R = 0.065
files = sorted(glob.glob("data/minute/options/*/*.parquet"))
start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
end = int(sys.argv[2]) if len(sys.argv) > 2 else len(files)
files = files[start:end]
print(f"Processing files [{start}:{end}], {len(files)} files this run...")

spot_cache = {
    "NIFTY": pd.read_parquet("data/minute/index/NIFTY.parquet")[['timestamp', 'close']].rename(columns={'close': 'spot'}),
    "BANKNIFTY": pd.read_parquet("data/minute/index/BANKNIFTY.parquet")[['timestamp', 'close']].rename(columns={'close': 'spot'}),
}

total_rows = 0
for fi, f in enumerate(files):
    parts = f.split('/')
    symbol, expiry = parts[-2], parts[-1].replace('.parquet', '')

    df = pd.read_parquet(f)
    df = df[df['close'] > 0]
    if df.empty:
        continue
    df = df.merge(spot_cache[symbol], on='timestamp', how='inner')
    if df.empty:
        continue

    df['dte'] = (pd.to_datetime(expiry) - pd.to_datetime(df['trading_day'])).dt.days.clip(lower=1) / 365.0
    df['moneyness'] = (df['strike'] - df['spot']) / df['spot'] * 100
    df['abs_moneyness'] = df['moneyness'].abs()

    S, K, T, price = df['spot'].values, df['strike'].values, df['dte'].values, df['close'].values
    is_call = (df['option_type'] == 'CE').values
    sigma = np.full(len(df), 0.20)
    for _ in range(30):
        d1 = (np.log(S/K) + (R+0.5*sigma**2)*T) / (sigma*np.sqrt(T))
        d2 = d1 - sigma*np.sqrt(T)
        cp = S*norm.cdf(d1) - K*np.exp(-R*T)*norm.cdf(d2)
        pp = K*np.exp(-R*T)*norm.cdf(-d2) - S*norm.cdf(-d1)
        model = np.where(is_call, cp, pp)
        vega = np.maximum(S*norm.pdf(d1)*np.sqrt(T), 1e-8)
        sigma = np.clip(sigma - np.clip((model-price)/vega, -1, 1), 1e-4, 5.0)
    df['iv'] = sigma

    # --- VECTORIZED ATM IV: idxmin per (timestamp, option_type) group ---
    atm_idx = df.groupby(['timestamp', 'option_type'])['abs_moneyness'].idxmin()
    atm_rows = df.loc[atm_idx, ['timestamp', 'option_type', 'iv']]
    atm_piv = atm_rows.pivot(index='timestamp', columns='option_type', values='iv')
    atm_piv['atm_iv'] = atm_piv.get('CE', np.nan).combine(atm_piv.get('PE', np.nan),
                                                            lambda a, b: (a+b)/2 if pd.notna(a) and pd.notna(b) else np.nan)

    # --- VECTORIZED skew: far OTM put/call, nearest to +-10% moneyness ---
    put_far = df[(df['option_type']=='PE') & (df['moneyness'] < -7) & (df['moneyness'] > -13)].copy()
    call_far = df[(df['option_type']=='CE') & (df['moneyness'] > 7) & (df['moneyness'] < 13)].copy()
    skew_series = pd.Series(dtype=float)
    if not put_far.empty and not call_far.empty:
        put_far['dist'] = (put_far['moneyness'] + 10).abs()
        call_far['dist'] = (call_far['moneyness'] - 10).abs()
        put_idx = put_far.groupby('timestamp')['dist'].idxmin()
        call_idx = call_far.groupby('timestamp')['dist'].idxmin()
        put_iv_by_ts = df.loc[put_idx].set_index('timestamp')['iv']
        call_iv_by_ts = df.loc[call_idx].set_index('timestamp')['iv']
        skew_series = (put_iv_by_ts - call_iv_by_ts).dropna()

    # --- Max-pain: still per-minute (genuine matrix op), but this part alone is fast ---
    max_pain_results = {}
    for ts, g in df.groupby('timestamp'):
        strikes = np.sort(g['strike'].unique())
        if len(strikes) < 3:
            continue
        ce = g[g['option_type']=='CE']; pe = g[g['option_type']=='PE']
        ce_s, ce_oi = ce['strike'].to_numpy(), ce['open_interest'].to_numpy()
        pe_s, pe_oi = pe['strike'].to_numpy(), pe['open_interest'].to_numpy()
        Kc = strikes[:, None]
        ce_pay = np.maximum(0, Kc - ce_s[None,:]) @ ce_oi if len(ce_s) else np.zeros(len(strikes))
        pe_pay = np.maximum(0, pe_s[None,:] - Kc) @ pe_oi if len(pe_s) else np.zeros(len(strikes))
        max_pain_results[ts] = float(strikes[np.argmin(ce_pay+pe_pay)])

    all_ts = df['timestamp'].unique()
    rows = []
    for ts in all_ts:
        a = atm_piv['atm_iv'].get(ts) if ts in atm_piv.index else None
        s = skew_series.get(ts)
        m = max_pain_results.get(ts)
        rows.append((symbol, expiry, pd.Timestamp(ts).isoformat(),
                     float(a) if pd.notna(a) else None,
                     float(s) if pd.notna(s) else None, m))

    c.executemany("INSERT OR REPLACE INTO minute_chain_signals VALUES (?,?,?,?,?,?)", rows)
    total_rows += len(rows)

    if (fi+1) % 20 == 0:
        conn.commit()
        print(f"  ...{fi+1}/{len(files)} files, {total_rows:,} rows, {time.time()-t0:.0f}s elapsed")

conn.commit()
print(f"\nThis run: {total_rows:,} rows, {time.time()-t0:.1f}s")
conn.close()
