"""
E6 -- Contract/expiry selection logic (Appendix B's roll rule).

Judgment call: Appendix B describes the rule as day-of-week arithmetic
(Thursday-cycle pre-2-Sep-2025, Tuesday-cycle after), which would need
hardcoding both regimes plus every NSE holiday shift by hand. Instead,
this reads the REAL listed expiries directly from daily_options for the
given date and picks the nearest one with at least min_dte days left --
mechanically identical to Appendix B's rule (never trade with <2 days
left, roll to the next cycle otherwise), but automatically correct
across the regime change and every holiday-shifted expiry, since the
data already reflects whatever the exchange's actual expiry dates were.
No day-of-week logic needed anywhere in this module.

Also handles the BANKNIFTY monthly-only regime (post-Nov-2024) the same
way, for free: the "listed expiries" query simply returns monthly
dates there instead of weekly ones, so the exact same function works
for both symbols without a special case.
"""
import sqlite3


def select_expiry(conn, symbol, date, min_dte=2):
    """
    Returns the nearest expiry with dte >= min_dte, chosen from the
    REAL listed expiries for this symbol on `date` -- not computed from
    a calendar. Raises if no real expiry with enough runway exists in
    the data (e.g. right at the very end of the available range).
    """
    rows = conn.execute("""
        SELECT DISTINCT expiry, dte FROM daily_options
        WHERE symbol=? AND date=? AND dte>=0
        ORDER BY dte
    """, (symbol, date)).fetchall()
    if not rows:
        raise ValueError(f"No listed expiries at all for {symbol} on {date}.")
    for expiry, dte in rows:
        if dte >= min_dte:
            return expiry
    raise ValueError(
        f"No expiry for {symbol} on {date} has >= {min_dte} DTE -- furthest listed is "
        f"{rows[-1][0]} at {rows[-1][1]} DTE. This can happen right at the edge of the "
        f"available data range; not silently returning a too-close expiry."
    )


if __name__ == "__main__":
    conn = sqlite3.connect("market.db")

    print("=== E6 self-check: real regime boundary, both symbols ===\n")

    # Case 1: NIFTY, well before the Sep-2025 Thursday->Tuesday change.
    # Appendix B says: entering with <2 DTE on the front contract rolls
    # to the following week -- verify the returned expiry actually has
    # >=2 DTE, using real listed data, no day-of-week math involved here.
    for date in ["2024-01-15", "2024-01-16", "2024-01-17"]:
        exp = select_expiry(conn, "NIFTY", date)
        row = conn.execute("SELECT dte FROM daily_options WHERE symbol='NIFTY' AND date=? AND expiry=?",
                            (date, exp)).fetchone()
        print(f"NIFTY {date} (pre-regime-change) -> expiry {exp}, dte={row[0]}")
        assert row[0] >= 2, f"REGRESSION: selected expiry has only {row[0]} DTE, rule requires >=2"

    print()
    # Case 2: dates straddling the actual 2-Sep-2025 regime change --
    # the function shouldn't need to know or care that the change
    # happened; it should just keep returning real, >=2-DTE expiries.
    for date in ["2025-08-28", "2025-09-02", "2025-09-08"]:
        exp = select_expiry(conn, "NIFTY", date)
        row = conn.execute("SELECT dte FROM daily_options WHERE symbol='NIFTY' AND date=? AND expiry=?",
                            (date, exp)).fetchone()
        print(f"NIFTY {date} (around real regime change) -> expiry {exp}, dte={row[0] if row else 'N/A'}")
        if row:
            assert row[0] >= 2, f"REGRESSION at regime boundary: only {row[0]} DTE"
    print("Check: expiry selection stays correct straddling the real regime change, with zero "
          "day-of-week logic in this module -> PASS\n")

    # Case 3: BANKNIFTY post-Nov-2024, monthly-only -- same function, no special case.
    exp = select_expiry(conn, "BANKNIFTY", "2025-06-10")
    row = conn.execute("SELECT dte FROM daily_options WHERE symbol='BANKNIFTY' AND date=? AND expiry=?",
                        ("2025-06-10", exp)).fetchone()
    print(f"BANKNIFTY 2025-06-10 (monthly-only regime) -> expiry {exp}, dte={row[0]}")
    assert row[0] >= 2
    print("Check: same function correctly handles BankNifty's monthly-only regime, no special-casing needed -> PASS")

    conn.close()
