"""
Extended daily signals: MA, RSI, MACD, ROC (multi-window), streak count,
gap size, realized vol (multi-window), IV-RV spread, VIX regime bucket,
PCR (OI + volume), OI buildup/unwind, futures rollover %.

Builds on top of C7's original daily_signals table (ret_1d/5d/12m,
dist_from_52w_high, atr14) - this adds the rest, same tables extended.
"""
import sqlite3
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, '.')
from minute_signals import rsi, macd, moving_average

conn = sqlite3.connect("market.db")
c = conn.cursor()

# ---------- Price/trend + volatility, from daily_spot ----------
spot = pd.read_sql("SELECT symbol, date, open, high, low, close FROM daily_spot ORDER BY symbol, date", conn)
spot['date'] = pd.to_datetime(spot['date'])
vix = pd.read_sql("SELECT date, close as vix_close FROM daily_vix", conn)
vix['date'] = pd.to_datetime(vix['date'])

ext_rows = []
for symbol, g in spot.groupby('symbol'):
    g = g.sort_values('date').reset_index(drop=True)

    g['ma_20'] = moving_average(g['close'], 20)
    g['ma_50'] = moving_average(g['close'], 50)
    g['ma_200'] = moving_average(g['close'], 200)
    g['rsi_14'] = rsi(g['close'], 14)
    macd_line, macd_sig, macd_hist = macd(g['close'])
    g['macd'], g['macd_signal'] = macd_line, macd_sig

    g['roc_3'] = g['close'].pct_change(3) * 100
    g['roc_10'] = g['close'].pct_change(10) * 100
    g['roc_20'] = g['close'].pct_change(20) * 100

    ret = g['close'].pct_change()
    direction = np.sign(ret)
    streak = direction.groupby((direction != direction.shift()).cumsum()).cumcount() + 1
    g['streak'] = streak * direction.fillna(0)  # positive = up-streak, negative = down-streak

    g['gap_pct'] = (g['open'] - g['close'].shift()) / g['close'].shift() * 100

    g['rv_10'] = ret.rolling(10).std() * np.sqrt(252) * 100
    g['rv_20'] = ret.rolling(20).std() * np.sqrt(252) * 100
    g['rv_30'] = ret.rolling(30).std() * np.sqrt(252) * 100

    for _, r in g.iterrows():
        ext_rows.append((symbol, r['date'].strftime('%Y-%m-%d'),
                          r['ma_20'], r['ma_50'], r['ma_200'], r['rsi_14'],
                          r['macd'], r['macd_signal'], r['roc_3'], r['roc_10'], r['roc_20'],
                          r['streak'], r['gap_pct'], r['rv_10'], r['rv_20'], r['rv_30']))

c.execute("DROP TABLE IF EXISTS daily_signals_ext")
c.execute("""CREATE TABLE daily_signals_ext (
    symbol TEXT, date TEXT, ma_20 REAL, ma_50 REAL, ma_200 REAL, rsi_14 REAL,
    macd REAL, macd_signal REAL, roc_3 REAL, roc_10 REAL, roc_20 REAL,
    streak REAL, gap_pct REAL, rv_10 REAL, rv_20 REAL, rv_30 REAL,
    PRIMARY KEY (symbol, date))""")
c.executemany("INSERT OR REPLACE INTO daily_signals_ext VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ext_rows)
conn.commit()
print(f"daily_signals_ext: {len(ext_rows)} rows")

# ---------- IV-RV spread (needs chain_signals' atm_iv + rv_20 just computed) ----------
cs = pd.read_sql("SELECT symbol, date, atm_iv FROM chain_signals", conn)
ext_df = pd.DataFrame(ext_rows, columns=['symbol','date','ma_20','ma_50','ma_200','rsi_14',
                                          'macd','macd_signal','roc_3','roc_10','roc_20',
                                          'streak','gap_pct','rv_10','rv_20','rv_30'])
merged = cs.merge(ext_df[['symbol','date','rv_20']], on=['symbol','date'], how='inner')
merged['iv_minus_rv'] = merged['atm_iv'] * 100 - merged['rv_20']  # atm_iv is decimal, rv_20 is already %

c.execute("DROP TABLE IF EXISTS iv_rv_spread")
c.execute("""CREATE TABLE iv_rv_spread (symbol TEXT, date TEXT, iv_minus_rv REAL, PRIMARY KEY (symbol, date))""")
c.executemany("INSERT OR REPLACE INTO iv_rv_spread VALUES (?,?,?)",
              [(r['symbol'], r['date'], r['iv_minus_rv']) for _, r in merged.iterrows() if pd.notna(r['iv_minus_rv'])])
conn.commit()
print(f"iv_rv_spread: {merged['iv_minus_rv'].notna().sum()} rows")

# ---------- VIX regime bucket ----------
vix = vix.sort_values('date')
vix['vix_rank_252'] = vix['vix_close'].rolling(252, min_periods=60).apply(lambda x: (x.iloc[-1] > x).mean()*100)
vix['vix_regime'] = pd.cut(vix['vix_rank_252'], bins=[-1,33,66,101], labels=['LOW','MEDIUM','HIGH'])
c.execute("DROP TABLE IF EXISTS vix_regime")
c.execute("""CREATE TABLE vix_regime (date TEXT PRIMARY KEY, vix_rank_252 REAL, vix_regime TEXT)""")
c.executemany("INSERT OR REPLACE INTO vix_regime VALUES (?,?,?)",
              [(r['date'].strftime('%Y-%m-%d'), r['vix_rank_252'], str(r['vix_regime']))
               for _, r in vix.iterrows() if pd.notna(r['vix_rank_252'])])
conn.commit()
print(f"vix_regime: {vix['vix_rank_252'].notna().sum()} rows")

# ---------- Daily PCR + OI buildup, from daily_options ----------
opts = pd.read_sql("SELECT symbol, date, option_type, contracts, oi FROM daily_options", conn)
daily_agg = opts.groupby(['symbol','date','option_type']).agg({'contracts':'sum','oi':'sum'}).reset_index()
piv = daily_agg.pivot_table(index=['symbol','date'], columns='option_type', values=['contracts','oi']).reset_index()
piv.columns = ['symbol','date','oi_ce','oi_pe','vol_ce','vol_pe']
piv['pcr_oi'] = piv['oi_pe'] / piv['oi_ce'].replace(0, np.nan)
piv['pcr_volume'] = piv['vol_pe'] / piv['vol_ce'].replace(0, np.nan)
piv = piv.sort_values(['symbol','date'])
piv['oi_change_ce'] = piv.groupby('symbol')['oi_ce'].diff()
piv['oi_change_pe'] = piv.groupby('symbol')['oi_pe'].diff()

c.execute("DROP TABLE IF EXISTS pcr_oi_signals")
c.execute("""CREATE TABLE pcr_oi_signals (
    symbol TEXT, date TEXT, pcr_oi REAL, pcr_volume REAL,
    oi_change_ce REAL, oi_change_pe REAL, PRIMARY KEY (symbol, date))""")
c.executemany("INSERT OR REPLACE INTO pcr_oi_signals VALUES (?,?,?,?,?,?)",
              [(r['symbol'], r['date'], r['pcr_oi'], r['pcr_volume'], r['oi_change_ce'], r['oi_change_pe'])
               for _, r in piv.iterrows()])
conn.commit()
print(f"pcr_oi_signals: {len(piv)} rows")

# ---------- Futures rollover % (daily only - no minute-level futures data exists) ----------
fut = pd.read_sql("SELECT symbol, date, expiry, oi FROM daily_futures", conn)
fut['date'] = pd.to_datetime(fut['date'])
rollover_rows = []
for symbol, g in fut.groupby('symbol'):
    for date, day_g in g.groupby('date'):
        day_g = day_g.sort_values('expiry')
        if len(day_g) < 2:
            continue
        near_oi = day_g.iloc[0]['oi']
        next_oi = day_g.iloc[1]['oi']
        total = near_oi + next_oi
        if total > 0:
            rollover_rows.append((symbol, date.strftime('%Y-%m-%d'), next_oi / total * 100))

c.execute("DROP TABLE IF EXISTS rollover_pct")
c.execute("""CREATE TABLE rollover_pct (symbol TEXT, date TEXT, rollover_pct REAL, PRIMARY KEY (symbol, date))""")
c.executemany("INSERT OR REPLACE INTO rollover_pct VALUES (?,?,?)", rollover_rows)
conn.commit()
print(f"rollover_pct: {len(rollover_rows)} rows (DAILY ONLY - no minute-level futures data exists to build this at minute granularity)")

conn.close()
print("\nDaily signal extension complete.")
