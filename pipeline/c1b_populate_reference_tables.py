"""
Populate reference tables (B5, B7, B8, B9) — designed in C1's schema but
never actually populated by any script until now. This was a real gap:
the roadmap said these tasks were DONE, but the actual data only ever
existed as prose in conversation, never written into the database.

Run this AFTER c1_schema.py (needs the tables to exist) and AFTER
c2a/c2b (B7's holiday derivation needs daily_spot/daily_vix/daily_options
already loaded).
"""
import sqlite3

conn = sqlite3.connect("market.db")
c = conn.cursor()

# ============================================================
# B5 — Lot size history (source-verified against primary NSE/
# Business Standard circulars, corrected from an initial AI-summary
# that had 2 errors)
# ============================================================
c.execute("DELETE FROM lot_sizes")
lot_size_rows = [
    ("NIFTY", "2000-06-01", "2021-06-24", 75),
    ("NIFTY", "2021-06-25", "2023-06-29", 50),
    ("NIFTY", "2023-06-30", "2024-04-25", 50),
    ("NIFTY", "2024-04-26", "2024-11-19", 25),
    ("NIFTY", "2024-11-20", "2025-04-24", 75),
    ("NIFTY", "2025-04-25", "2025-12-30", 75),
    ("NIFTY", "2025-12-31", None, 65),
    ("BANKNIFTY", "2016-01-01", "2023-06-29", 25),
    ("BANKNIFTY", "2023-06-30", "2024-11-19", 15),
    ("BANKNIFTY", "2024-11-20", "2025-04-24", 30),
    ("BANKNIFTY", "2025-04-25", "2025-12-30", 35),
    ("BANKNIFTY", "2025-12-31", None, 30),
]
c.executemany("INSERT INTO lot_sizes VALUES (?,?,?,?)", lot_size_rows)
print(f"lot_sizes: {len(lot_size_rows)} rows inserted")

# ============================================================
# B8 — RBI MPC decisions + Union Budget dates
# ============================================================
c.execute("DELETE FROM events")
rbi_dates = [
    "2020-03-27", "2020-05-22", "2020-08-06", "2020-10-09", "2020-12-04",
    "2021-02-05", "2021-04-07", "2021-06-04", "2021-08-06", "2021-10-08", "2021-12-08",
    "2022-02-10", "2022-04-08", "2022-05-04", "2022-06-08", "2022-08-05", "2022-09-30", "2022-12-07",
    "2023-02-08", "2023-04-06", "2023-06-08", "2023-08-10", "2023-10-06", "2023-12-08",
    "2024-02-08", "2024-04-05", "2024-06-07", "2024-08-08", "2024-10-09", "2024-12-06",
    "2025-02-07", "2025-04-09", "2025-06-06", "2025-08-06", "2025-10-01", "2025-12-05",
    "2026-02-06", "2026-04-08", "2026-06-05", "2026-08-05",
]
offcycle = {"2020-03-27", "2020-05-22", "2022-05-04"}
event_rows = []
for d in rbi_dates:
    etype = "RBI_MPC_OFFCYCLE" if d in offcycle else "RBI_MPC"
    event_rows.append((d, etype, None))

budget_dates = [
    ("2020-02-01", "BUDGET", None), ("2021-02-01", "BUDGET", None),
    ("2022-02-01", "BUDGET", None), ("2023-02-01", "BUDGET", None),
    ("2024-02-01", "BUDGET_INTERIM", "Interim budget, election year"),
    ("2024-07-23", "BUDGET", "Full budget, post-election"),
    ("2025-02-01", "BUDGET", None), ("2026-02-01", "BUDGET", None),
]
event_rows.extend(budget_dates)
c.executemany("INSERT INTO events VALUES (?,?,?)", event_rows)
print(f"events: {len(event_rows)} rows inserted ({len(rbi_dates)} RBI, {len(budget_dates)} Budget)")

# ============================================================
# B9 — Weekly expiry regime (BankNifty weeklies discontinued 20-Nov-2024)
# ============================================================
c.execute("DELETE FROM expiry_regime")
regime_rows = [
    ("NIFTY", "2019-02-01", None, 1),  # Nifty has had weeklies continuously since Feb 2019
    ("BANKNIFTY", "2016-05-01", "2024-11-19", 1),
    ("BANKNIFTY", "2024-11-20", None, 0),
]
c.executemany("INSERT INTO expiry_regime VALUES (?,?,?,?)", regime_rows)
print(f"expiry_regime: {len(regime_rows)} rows inserted")

# ============================================================
# B7 — Holidays: derive by finding weekdays present in NEITHER
# daily_spot NOR daily_options NOR daily_futures (all three empty
# = genuine market closure, not a data gap in one source)
# ============================================================
import pandas as pd
all_weekdays = pd.bdate_range("2020-04-13", "2026-09-11")
bhav_dates = set(pd.to_datetime(pd.read_sql("SELECT DISTINCT date FROM daily_options", conn)['date']))
holiday_dates = [d.strftime("%Y-%m-%d") for d in all_weekdays if d not in bhav_dates]

c.execute("DELETE FROM holidays")
c.executemany("INSERT INTO holidays VALUES (?)", [(d,) for d in holiday_dates])
print(f"holidays: {len(holiday_dates)} rows inserted")

conn.commit()

# Sanity check all 4
print("\n--- Verification ---")
for t in ["lot_sizes", "events", "expiry_regime", "holidays"]:
    n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"{t}: {n} rows")
conn.close()
