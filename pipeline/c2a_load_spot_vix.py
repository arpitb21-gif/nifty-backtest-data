"""
C2a — Load daily spot (Nifty, BankNifty) and VIX CSVs into SQLite.
"""
import sqlite3, csv

conn = sqlite3.connect("market.db")
c = conn.cursor()

def load_spot(symbol, path):
    n = 0
    with open(path) as f:
        for row in csv.DictReader(f):
            c.execute(
                "INSERT OR REPLACE INTO daily_spot VALUES (?,?,?,?,?,?,?)",
                (symbol, row['date'], float(row['open']), float(row['high']),
                 float(row['low']), float(row['close']),
                 float(row['volume']) if row.get('volume') else None)
            )
            n += 1
    return n

def load_vix(path):
    n = 0
    with open(path) as f:
        for row in csv.DictReader(f):
            c.execute(
                "INSERT OR REPLACE INTO daily_vix VALUES (?,?,?,?,?)",
                (row['date'], float(row['open']), float(row['high']),
                 float(row['low']), float(row['close']))
            )
            n += 1
    return n

n1 = load_spot("NIFTY", "data/daily/nifty_spot_daily_2020-04-01_to_2026-09-15.csv")
n2 = load_spot("BANKNIFTY", "data/daily/banknifty_spot_daily_2020-04-01_to_2026-09-15.csv")
n3 = load_vix("data/daily/india_vix_daily_2020-04-01_to_2026-09-15.csv")

conn.commit()
print(f"Loaded: NIFTY spot={n1}, BANKNIFTY spot={n2}, VIX={n3}")

# Sanity check
for tbl in ['daily_spot', 'daily_vix']:
    cnt = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    print(f"{tbl}: {cnt} rows")
conn.close()
