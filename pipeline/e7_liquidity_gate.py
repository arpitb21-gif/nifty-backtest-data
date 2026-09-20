"""
E7 -- Liquidity filter + data-availability gate, run BEFORE any
strategy's entry trigger is evaluated (Appendix B.5's centralization
requirement -- this is a pre-entry check, separate from E3's job of
managing an ALREADY-OPEN position's lifecycle).

REVISED per Arpit's direction after review: not a blanket rule. Each
strategy declares what it actually needs:

- requires_intraday=True -- the strategy's own definition genuinely
  needs intraday data to mean anything at all (Opening Range Breakout:
  needs the real 09:15-09:44 range; Expiry-Day Pinning: enters at a
  specific intraday timestamp on expiry day). There is no meaningful
  daily approximation for these -- if minute data isn't available for
  that leg/date, that trade is SKIPPED for that date. Nothing else is
  skipped; other strategies, and this same strategy on other dates,
  proceed normally.

- requires_intraday=False (the default -- every short-premium strategy
  in Section 2 whose ENTRY signal is daily: Conditional VRP, Iron
  Condor, etc.) -- minute data only sharpens stop/target precision, it
  isn't required for the strategy to be meaningful. If unavailable,
  the trade still goes ahead; E3's daily fallback handles it, clearly
  labeled (granularity="daily" in every result).

Liquidity itself stays a HARD gate regardless of requires_intraday --
an illiquid strike is never tradeable, that's not a data-availability
question.
"""
import sqlite3
from e1_position import get_option_row, load_minute_chain, minute_chain_quality


def check_entry(conn, symbol, date, expiry, legs, requires_intraday=False,
                 minute_dir="data/minute/options"):
    """
    legs: list of (strike, option_type) about to be opened.
    requires_intraday: set True only for strategies that genuinely
        cannot function without real intraday data (ORB, Expiry-Day
        Pinning). Everything else leaves this False.

    Returns {"eligible": bool, "reason": str or None, "granularity_plan": [...]}.
    """
    for strike, option_type in legs:
        row = get_option_row(conn, symbol, date, expiry, strike, option_type)
        if row is None:
            return {"eligible": False, "reason": f"no data at all for {strike}{option_type} on {date}",
                    "granularity_plan": []}
        if row["is_liquid"] != 1:
            return {"eligible": False,
                    "reason": f"{strike}{option_type} fails liquidity filter (is_liquid={row['is_liquid']})",
                    "granularity_plan": []}

    granularity_plan = []
    any_missing_minute = False
    for strike, option_type in legs:
        chain = load_minute_chain(symbol, expiry, strike, option_type, minute_dir)
        ok, reason = minute_chain_quality(chain)
        if not ok:
            any_missing_minute = True
        granularity_plan.append({
            "leg": f"{strike}{option_type}",
            "expected_granularity": "minute" if ok else "daily",
            "note": None if ok else reason,
        })

    if requires_intraday and any_missing_minute:
        missing_legs = [p["leg"] for p in granularity_plan if p["expected_granularity"] == "daily"]
        return {"eligible": False,
                "reason": f"strategy requires intraday data but it's unavailable for: {missing_legs} "
                          f"on {date} -- skipping this date only, not the whole week/period",
                "granularity_plan": granularity_plan}

    return {"eligible": True, "reason": None, "granularity_plan": granularity_plan}


if __name__ == "__main__":
    conn = sqlite3.connect("market.db")

    print("=== E7 self-check 1: real liquid legs -> eligible, minute granularity expected ===\n")
    r1 = check_entry(conn, "NIFTY", "2026-05-25", "2026-06-02", [(24500.0, "CE"), (23600.0, "PE")])
    print(r1)
    assert r1["eligible"] is True
    assert all(p["expected_granularity"] == "minute" for p in r1["granularity_plan"])
    print("Check: eligible, both legs correctly flagged for minute-granularity tracking -> PASS\n")

    print("=== E7 self-check 2: real known-sparse week -> still eligible (liquidity is separate "
          "from minute-data quality), but daily granularity correctly flagged ===\n")
    r2 = check_entry(conn, "NIFTY", "2026-06-03", "2026-06-09", [(23900.0, "CE"), (23050.0, "PE")])
    print(r2)
    assert r2["eligible"] is True, "liquidity should still pass here -- this is a minute-DATA gap, not an illiquid strike"
    assert all(p["expected_granularity"] == "daily" for p in r2["granularity_plan"])
    print("Check: correctly stays eligible (soft signal, not a hard block) and flags daily fallback -> PASS\n")

    print("=== E7 self-check 3: real deep-OTM illiquid strike -> hard reject regardless of mode ===\n")
    # From earlier verification in this project: deep-ITM strikes with
    # near-zero volume genuinely fail is_liquid on ordinary days.
    r3 = check_entry(conn, "NIFTY", "2024-01-15", "2024-01-18", [(19650.0, "CE")])
    print(r3)
    assert r3["eligible"] is False
    print("Check: real illiquid strike correctly hard-rejected -> PASS\n")

    print("=== E7 self-check 4: requires_intraday=True on the known-sparse week -> SKIPPED, "
          "not a daily fallback (this is the strategy-aware behavior Arpit specified) ===\n")
    r4 = check_entry(conn, "NIFTY", "2026-06-03", "2026-06-09", [(23900.0, "CE"), (23050.0, "PE")],
                      requires_intraday=True)
    print(r4)
    assert r4["eligible"] is False, "an intraday-required strategy must be skipped, not silently fall back"
    print("Check: intraday-required strategy correctly skipped for this date only -> PASS\n")

    print("=== E7 self-check 5: requires_intraday=False (default) on the SAME sparse week -> "
          "still eligible, daily fallback flagged (a different strategy, same date, isn't skipped) ===\n")
    r5 = check_entry(conn, "NIFTY", "2026-06-03", "2026-06-09", [(23900.0, "CE"), (23050.0, "PE")],
                      requires_intraday=False)
    print(r5)
    assert r5["eligible"] is True, "a daily-signal strategy should still be able to trade using daily fallback"
    print("Check: same date, ordinary strategy still trades (daily fallback), only the "
          "intraday-dependent strategy was skipped -> PASS")

    conn.close()
