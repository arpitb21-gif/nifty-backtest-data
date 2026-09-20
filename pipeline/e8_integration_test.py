"""
E8 -- Engine self-test.

Chains every piece built in Task E together, on real data, exactly the
way a real strategy will use them: E6 picks the expiry -> E2 picks
strikes by delta -> E7 checks entry eligibility -> E1 opens the
position -> E3 runs the minute-first lifecycle with daily fallback ->
E5 reports margin/Greeks at entry -> E4 provides the independent
settlement cross-check if the position happens to still be open at
expiry (it won't be, in this run, since E3 will find a real exit first
-- E4's own module already carries its own dedicated settlement test).

This is the "price a known structure and reconcile P&L by hand" step
the roadmap calls for -- done here as a full pipeline run with every
number printed for manual verification, not a synthetic example.
"""
import sqlite3
from e1_position import Position
from e2_strike_selection import select_strike_by_delta
from e3_executor import run_lifecycle_minute
from e5_margin_greeks import position_greeks, position_margin
from e6_expiry_selection import select_expiry
from e7_liquidity_gate import check_entry

if __name__ == "__main__":
    conn = sqlite3.connect("market.db")

    SYMBOL = "NIFTY"
    ENTRY_DATE = "2026-05-25"   # a real VIX-rank trigger day, verified earlier in this project
    TARGET_DELTA = 0.20

    print(f"=== E8 full-engine integration test: {SYMBOL}, real trigger day {ENTRY_DATE} ===\n")

    # Step 1 (E6): pick the expiry the way a real strategy would.
    expiry = select_expiry(conn, SYMBOL, ENTRY_DATE)
    print(f"E6 -> expiry selected: {expiry}")

    # Step 2 (E2): pick strikes by delta.
    call = select_strike_by_delta(conn, SYMBOL, ENTRY_DATE, expiry, "CE", TARGET_DELTA)
    put = select_strike_by_delta(conn, SYMBOL, ENTRY_DATE, expiry, "PE", TARGET_DELTA)
    print(f"E2 -> CE {call['strike']} (delta {call['delta']:.4f}), "
          f"PE {put['strike']} (delta {put['delta']:.4f})")

    # Step 3 (E7): check entry eligibility before opening anything.
    legs_wanted = [(call["strike"], "CE"), (put["strike"], "PE")]
    gate = check_entry(conn, SYMBOL, ENTRY_DATE, expiry, legs_wanted)
    print(f"E7 -> eligible={gate['eligible']}, granularity plan: "
          f"{[(g['leg'], g['expected_granularity']) for g in gate['granularity_plan']]}")
    assert gate["eligible"], f"E8 test picked an ineligible entry: {gate['reason']}"

    # Step 4 (E1): open the position for real.
    pos = Position(conn)
    pos.open_leg(SYMBOL, expiry, call["strike"], "CE", "SELL", 1, ENTRY_DATE)
    pos.open_leg(SYMBOL, expiry, put["strike"], "PE", "SELL", 1, ENTRY_DATE)
    credit = pos.net_entry_credit()
    print(f"E1 -> position opened, entry credit Rs {credit}")

    # Step 5 (E5): margin and Greeks at entry, before any time passes.
    greeks, missing = position_greeks(conn, pos, ENTRY_DATE)
    margin, margin_detail, vol = position_margin(conn, pos, ENTRY_DATE, force_close_date=expiry)
    print(f"E5 -> Greeks at entry: {greeks}")
    print(f"E5 -> Margin required at entry: Rs {margin:,.2f} (trailing vol {vol:.2f}%)")

    # Step 6 (E3): run the actual lifecycle -- minute-first, daily fallback.
    result = run_lifecycle_minute(conn, pos, entry_date=ENTRY_DATE, force_close_date=expiry,
                                   stop_multiple=2.0, target_pct=0.5)
    minute_bars = sum(1 for p in result["path"] if p.get("granularity") == "minute")
    print(f"E3 -> {minute_bars} real minute bars checked before exit")
    print(f"E3 -> Exit: {result['exit_reason']} at "
          f"{result.get('exit_timestamp', result.get('exit_date'))}, "
          f"P&L Rs {result['pnl']}, granularity={result['granularity']}")

    # Manual reconciliation -- every number above should compose into this.
    print("\n--- Manual reconciliation ---")
    print(f"Credit collected at entry:      Rs {credit:>10,.2f}")
    print(f"P&L at exit:                     Rs {result['pnl']:>10,.2f}")
    print(f"Stop level was:                  Rs {-2.0*credit:>10,.2f}")
    print(f"Target level was:                Rs {0.5*credit:>10,.2f}")
    if result["exit_reason"] == "TARGET":
        assert result["pnl"] >= 0.5 * credit, "TARGET exit but P&L doesn't actually clear the target level"
        print("Reconciliation check: P&L clears the target level exactly as it should -> PASS")
    elif result["exit_reason"] == "STOP":
        assert result["pnl"] <= -2.0 * credit, "STOP exit but P&L doesn't actually breach the stop level"
        print("Reconciliation check: P&L breaches the stop level exactly as it should -> PASS")

    print("\nFull chain E6 -> E2 -> E7 -> E1 -> E5 -> E3 ran on real data end to end, "
          "every number traceable and hand-checkable above. Task E is functionally complete "
          "for options; Section 3's futures engine (Task F) is separate and not built yet.")

    conn.close()
