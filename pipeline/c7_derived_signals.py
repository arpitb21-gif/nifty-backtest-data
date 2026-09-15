"""
C7 — Derived signals: IV rank, skew percentile, max-pain, ATR, z-scores,
rolling returns. These feed strategy trigger conditions directly.

ASSUMPTION (logged): "ATM IV" for IV-rank purposes = the average IV of the
two options (one CE, one PE) closest to zero moneyness on each date, for
the nearest weekly-ish expiry (smallest DTE >= 3, to avoid expiry-day
noise). This proxies India VIX's own methodology reasonably well but is
NOT India VIX itself - we already HAVE real VIX data (daily_vix table),
so Conditional VRP's actual "IV rank" trigger should use VIX rank, not
this proxy. This proxy is built for skew/other option-chain-derived
signals that need a same-day ATM reference, not as a VIX replacement.

ASSUMPTION (logged): skew = IV(strike closest to -10% moneyness, i.e. a
far OTM put) minus IV(strike closest to +10% moneyness, i.e. a far OTM
call), on the nearest weekly-ish expiry. This is a simplified proxy for
the "10-delta vs 25-delta put skew" described in the roadmap spec, using
moneyness distance instead of computed delta, since filtering by exact
delta bands significantly reduces sample size on days with sparse strikes.
Logged as a simplification, not a silent substitution.

ASSUMPTION (logged): max-pain computed only for the SAME nearest weekly-
ish expiry used above, once per (symbol, date). Standard OI-weighted
payout-to-writers minimization across all strikes present that day.
"""
import sqlite3
import pandas as pd
import numpy as np

conn = sqlite3.connect("market.db")

# ---------- Rolling returns + ATR (from spot) ----------
spot = pd.read_sql("SELECT symbol, date, open, high, low, close FROM daily_spot ORDER BY symbol, date", conn)
spot['date'] = pd.to_datetime(spot['date'])

sig_rows = []
for symbol, g in spot.groupby('symbol'):
    g = g.sort_values('date').reset_index(drop=True)
    g['ret_1d'] = g['close'].pct_change()
    g['ret_5d'] = g['close'].pct_change(5)
    g['ret_12m'] = g['close'].pct_change(252)
    g['ret_12m_ex1m'] = g['close'].shift(21) / g['close'].shift(252) - 1  # 12-1 momentum
    g['high_52w'] = g['close'].rolling(252, min_periods=50).max()
    g['dist_from_52w_high'] = (g['close'] - g['high_52w']) / g['high_52w'] * 100

    tr = pd.concat([
        g['high'] - g['low'],
        (g['high'] - g['close'].shift()).abs(),
        (g['low'] - g['close'].shift()).abs()
    ], axis=1).max(axis=1)
    g['atr14'] = tr.rolling(14, min_periods=5).mean()

    for _, r in g.iterrows():
        sig_rows.append((symbol, r['date'].strftime('%Y-%m-%d'),
                          r['ret_1d'], r['ret_5d'], r['ret_12m_ex1m'],
                          r['dist_from_52w_high'], r['atr14']))

c = conn.cursor()
c.execute("DROP TABLE IF EXISTS daily_signals")
c.execute("""CREATE TABLE daily_signals (
    symbol TEXT, date TEXT, ret_1d REAL, ret_5d REAL, ret_12m_ex1m REAL,
    dist_from_52w_high REAL, atr14 REAL, PRIMARY KEY (symbol, date))""")
c.executemany("INSERT INTO daily_signals VALUES (?,?,?,?,?,?,?)", sig_rows)
conn.commit()
print(f"daily_signals: {len(sig_rows)} rows")

# ---------- Nifty-BankNifty spread z-score ----------
piv = spot.pivot(index='date', columns='symbol', values='close').dropna()
piv['ratio'] = piv['BANKNIFTY'] / piv['NIFTY']
piv['zscore_60d'] = (piv['ratio'] - piv['ratio'].rolling(60).mean()) / piv['ratio'].rolling(60).std()
piv = piv.reset_index()

c.execute("DROP TABLE IF EXISTS spread_signals")
c.execute("""CREATE TABLE spread_signals (
    date TEXT PRIMARY KEY, ratio REAL, zscore_60d REAL)""")
c.executemany("INSERT INTO spread_signals VALUES (?,?,?)",
              [(r['date'].strftime('%Y-%m-%d'), r['ratio'], r['zscore_60d'])
               for _, r in piv.iterrows() if pd.notna(r['zscore_60d'])])
conn.commit()
print(f"spread_signals: {piv['zscore_60d'].notna().sum()} rows")

# ---------- ATM IV proxy, skew, max-pain (per symbol/date, nearest expiry DTE>=3) ----------
opts = pd.read_sql("""
    SELECT symbol, date, expiry, strike, option_type, moneyness, dte, iv, oi
    FROM daily_options WHERE dte >= 3 AND iv IS NOT NULL
""", conn)

results_atm, results_maxpain = [], []
groups = list(opts.groupby(['symbol', 'date']))
print(f"Processing {len(groups)} (symbol, date) groups for ATM/skew/max-pain...")

for i, ((symbol, date), g) in enumerate(groups):
    nearest_expiry = g.loc[g['dte'].idxmin(), 'expiry']
    chain = g[g['expiry'] == nearest_expiry]
    if chain.empty:
        continue

    ce = chain[chain['option_type'] == 'CE']
    pe = chain[chain['option_type'] == 'PE']

    # ATM proxy: avg IV of CE/PE closest to 0 moneyness
    atm_iv = None
    if not ce.empty and not pe.empty:
        ce_iv = ce.loc[ce['moneyness'].abs().idxmin(), 'iv']
        pe_iv = pe.loc[pe['moneyness'].abs().idxmin(), 'iv']
        atm_iv = (ce_iv + pe_iv) / 2

    # Skew: far OTM put IV minus far OTM call IV (~10% moneyness each side)
    skew = None
    put_far = pe[(pe['moneyness'] < -7) & (pe['moneyness'] > -13)]
    call_far = ce[(ce['moneyness'] > 7) & (ce['moneyness'] < 13)]
    if not put_far.empty and not call_far.empty:
        put_iv = put_far.loc[(put_far['moneyness'] + 10).abs().idxmin(), 'iv']
        call_iv = call_far.loc[(call_far['moneyness'] - 10).abs().idxmin(), 'iv']
        skew = put_iv - call_iv

    results_atm.append((symbol, date, nearest_expiry, atm_iv, skew))

    # Max pain — vectorized with numpy instead of a per-strike pandas filter loop
    strikes = np.sort(chain['strike'].unique())
    if len(strikes) >= 3:
        ce_strikes = ce['strike'].to_numpy()
        ce_oi = ce['oi'].to_numpy()
        pe_strikes = pe['strike'].to_numpy()
        pe_oi = pe['oi'].to_numpy()
        # payout[k] = sum over CE of max(0, K - ce_strike)*oi + sum over PE of max(0, pe_strike - K)*oi
        K = strikes[:, None]
        ce_payout = np.maximum(0, K - ce_strikes[None, :]) @ ce_oi if len(ce_strikes) else np.zeros(len(strikes))
        pe_payout = np.maximum(0, pe_strikes[None, :] - K) @ pe_oi if len(pe_strikes) else np.zeros(len(strikes))
        total_payout = ce_payout + pe_payout
        max_pain_strike = float(strikes[np.argmin(total_payout)])
        results_maxpain.append((symbol, date, nearest_expiry, max_pain_strike))

    if (i + 1) % 500 == 0:
        print(f"  ...{i+1}/{len(groups)} groups done")

c.execute("DROP TABLE IF EXISTS chain_signals")
c.execute("""CREATE TABLE chain_signals (
    symbol TEXT, date TEXT, expiry TEXT, atm_iv REAL, skew REAL,
    PRIMARY KEY (symbol, date))""")
c.executemany("INSERT INTO chain_signals VALUES (?,?,?,?,?)", results_atm)

c.execute("DROP TABLE IF EXISTS max_pain")
c.execute("""CREATE TABLE max_pain (
    symbol TEXT, date TEXT, expiry TEXT, max_pain_strike REAL,
    PRIMARY KEY (symbol, date))""")
c.executemany("INSERT INTO max_pain VALUES (?,?,?,?)", results_maxpain)
conn.commit()
print(f"chain_signals: {len(results_atm)} rows, max_pain: {len(results_maxpain)} rows")

# ---------- IV rank and skew rank (trailing 252-day percentile) ----------
cs = pd.read_sql("SELECT * FROM chain_signals ORDER BY symbol, date", conn)
cs['date'] = pd.to_datetime(cs['date'])
rank_rows = []
for symbol, g in cs.groupby('symbol'):
    g = g.sort_values('date').reset_index(drop=True)
    g['iv_rank_252'] = g['atm_iv'].rolling(252, min_periods=60).apply(
        lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
    g['skew_rank_252'] = g['skew'].rolling(252, min_periods=60).apply(
        lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
    for _, r in g.iterrows():
        rank_rows.append((symbol, r['date'].strftime('%Y-%m-%d'),
                           r['iv_rank_252'], r['skew_rank_252']))

c.execute("DROP TABLE IF EXISTS rank_signals")
c.execute("""CREATE TABLE rank_signals (
    symbol TEXT, date TEXT, iv_rank_252 REAL, skew_rank_252 REAL,
    PRIMARY KEY (symbol, date))""")
c.executemany("INSERT INTO rank_signals VALUES (?,?,?,?)",
              [r for r in rank_rows if pd.notna(r[2]) or pd.notna(r[3])])
conn.commit()
print(f"rank_signals: {len(rank_rows)} rows")

conn.close()
print("\nC7 complete.")
