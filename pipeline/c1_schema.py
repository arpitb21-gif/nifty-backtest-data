"""
C1 — SQLite schema design.

ASSUMPTION (logged): Minute-level options/index data (items 5-8 in COVERAGE.md,
~4.8GB of Parquet) is NOT loaded into SQLite. It stays as Parquet, queried
directly via DuckDB/pandas when needed. Reason: only 1 of 14 strategies
(Expiry-Day Pinning) needs minute data, and dumping ~750M rows into SQLite
would be slow to build and query for no benefit to the other 13 tasks.
SQLite holds everything daily-frequency: spot, futures, options, and all
reference/calendar data. This is a deliberate deviation from the original
roadmap wording ("Tables for spot, options chains, futures... " implied
one system for everything) — flagging it here rather than silently doing it.
"""
import sqlite3
import os

DB_PATH = "market.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

c.executescript("""
-- Daily index spot prices (Nifty, BankNifty)
CREATE TABLE daily_spot (
    symbol      TEXT NOT NULL,      -- 'NIFTY' or 'BANKNIFTY'
    date        TEXT NOT NULL,      -- ISO 'YYYY-MM-DD'
    open        REAL, high REAL, low REAL, close REAL,
    volume      REAL,
    PRIMARY KEY (symbol, date)
);

-- Daily India VIX
CREATE TABLE daily_vix (
    date        TEXT PRIMARY KEY,
    open        REAL, high REAL, low REAL, close REAL
);

-- Daily futures, one row per contract per day (from bhavcopy FUTIDX rows)
CREATE TABLE daily_futures (
    symbol      TEXT NOT NULL,      -- NIFTY / BANKNIFTY
    date        TEXT NOT NULL,
    expiry      TEXT NOT NULL,      -- ISO date of contract expiry
    open        REAL, high REAL, low REAL, close REAL, settle REAL,
    contracts   INTEGER, val_inlakh REAL, oi INTEGER, chg_oi INTEGER,
    PRIMARY KEY (symbol, date, expiry)
);

-- Daily options, one row per (symbol, date, expiry, strike, type)
CREATE TABLE daily_options (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    expiry      TEXT NOT NULL,
    strike      REAL NOT NULL,
    option_type TEXT NOT NULL,      -- 'CE' or 'PE'
    open        REAL, high REAL, low REAL, close REAL, settle REAL,
    contracts   INTEGER, val_inlakh REAL, oi INTEGER, chg_oi INTEGER,
    -- derived, filled in by C5/C6:
    iv          REAL,
    delta       REAL, gamma REAL, theta REAL, vega REAL,
    PRIMARY KEY (symbol, date, expiry, strike, option_type)
);

-- Lot size history (B5) — date-ranged, so a lookup is "active row covering this date"
CREATE TABLE lot_sizes (
    symbol      TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT,               -- NULL = still active
    lot_size    INTEGER NOT NULL
);

-- Trading holidays (B7, derived)
CREATE TABLE holidays (
    date        TEXT PRIMARY KEY
);

-- Events: RBI MPC decisions + Union Budget dates (B8)
CREATE TABLE events (
    date        TEXT NOT NULL,
    event_type  TEXT NOT NULL,      -- 'RBI_MPC' | 'RBI_MPC_OFFCYCLE' | 'BUDGET' | 'BUDGET_INTERIM'
    note        TEXT,
    PRIMARY KEY (date, event_type)
);

-- Weekly expiry rule regime (B9) — for filtering which symbols had weekly
-- expiries active on a given date
CREATE TABLE expiry_regime (
    symbol         TEXT NOT NULL,
    start_date     TEXT NOT NULL,
    end_date       TEXT,
    weekly_active  INTEGER NOT NULL  -- 1 = weekly expiries existed, 0 = monthly only
);

CREATE INDEX idx_futures_date ON daily_futures(date);
CREATE INDEX idx_options_date ON daily_options(date);
CREATE INDEX idx_options_expiry ON daily_options(symbol, expiry);
""")

conn.commit()
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables created:", [t[0] for t in tables])
conn.close()
