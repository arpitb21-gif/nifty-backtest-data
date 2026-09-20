"""
E3 -- Entry and exit executor.

Two entry points:

- run_lifecycle_minute(...) -- the one options strategies should use.
  Walks a position MINUTE BY MINUTE using each leg's own real intraday
  price series wherever it exists, checking stop/target continuously
  rather than once per day. Falls back to the daily path automatically
  for any stretch minute data doesn't cover -- including, always, the
  actual expiry day itself: confirmed during this build that a given
  expiry contract's minute file stops the trading day BEFORE its own
  expiry (e.g. the 2026-06-02 file's last real day is 2026-05-29), so
  even a fully-minute-covered position needs at least one daily-settle
  lookup at the very end. This corrects an earlier version of this
  module that checked stop/target once per day only -- a real mistake,
  caught before any strategy was actually run on it.

- run_lifecycle(...) -- the original daily-only walker, kept as-is.
  Used internally as run_lifecycle_minute's fallback once minute data
  runs out, and still the right tool directly for anything that
  genuinely has no applicable minute data (there isn't a futures
  version of this at all -- Section 3 stays daily throughout, since no
  minute-level futures data exists in any source).

Trading days come from daily_options itself (distinct dates present for
that symbol+expiry), not a generated weekday range -- this automatically
respects real holidays without needing a separate calendar join, since
bhavcopy simply has no row on a holiday.

Stop/target convention, matched to how every strategy spec in the
roadmap phrases it ("Stop at 2.0x net credit", "Target 50% of max
profit"): both are expressed as a multiple/fraction of the entry credit,
which only makes sense for a net-credit (short-premium) position. A
net-debit position needs a different convention -- NOT handled by this
module yet; it raises rather than silently applying the wrong formula.

Data-availability guard: run_lifecycle_minute uses minute data for a
leg only if minute_chain_quality() passes (see E1) -- a known-sparse
file (e.g. the 1-day 2026-06-09 file) is treated as "no minute data for
this leg" and the whole position falls back to daily immediately,
rather than trusting a chain that isn't real week-long coverage. This
is a LOCAL guard, not the full E7 centralized data-availability gate
(which belongs at the strategy-entry level, before a position is even
opened, and still needs building) -- it just stops this module from
silently trusting bad data if E7 hasn't already screened it out.
"""
import sqlite3
import pandas as pd
from e1_position import Position, load_minute_chain, minute_chain_quality


def get_trading_days(conn, symbol, expiry, start_date, end_date):
    rows = conn.execute("""
        SELECT DISTINCT date FROM daily_options
        WHERE symbol=? AND expiry=? AND date>=? AND date<=?
        ORDER BY date
    """, (symbol, expiry, start_date, end_date)).fetchall()
    return [r[0] for r in rows]


def _daily_walk(conn, position, start_date, force_close_date, stop_level, target_level, path):
    """Shared daily-granularity loop, appends to an existing path list."""
    symbol = position.legs[0].symbol
    expiry = position.legs[0].expiry
    days = get_trading_days(conn, symbol, expiry, start_date, force_close_date)
    if not days:
        raise ValueError(
            f"No trading day for {symbol} {expiry} on or after {start_date} "
            f"was reached (force_close_date={force_close_date})."
        )
    for date in days:
        pnl, detail = position.mark_to_market(date)
        path.append({"date": date, "pnl": pnl, "granularity": "daily"})
        if date == force_close_date:
            return {"exit_date": date, "exit_reason": "FORCE_CLOSE",
                    "pnl": pnl, "granularity": "daily", "path": path}
        if pnl <= stop_level:
            return {"exit_date": date, "exit_reason": "STOP",
                    "pnl": pnl, "granularity": "daily", "path": path}
        if pnl >= target_level:
            return {"exit_date": date, "exit_reason": "TARGET",
                    "pnl": pnl, "granularity": "daily", "path": path}
    raise ValueError(
        f"Ran out of trading days before reaching force_close_date={force_close_date}; "
        f"last day checked was {days[-1]}."
    )


def run_lifecycle(conn, position, start_date, force_close_date, stop_multiple, target_pct):
    """Original daily-only walker. See module docstring."""
    credit = position.net_entry_credit()
    if credit <= 0:
        raise ValueError(
            f"run_lifecycle only handles net-credit positions; this position's "
            f"net_entry_credit is Rs {credit}."
        )
    stop_level = -stop_multiple * credit
    target_level = target_pct * credit
    return _daily_walk(conn, position, start_date, force_close_date, stop_level, target_level, [])


def run_lifecycle_minute(conn, position, entry_date, force_close_date,
                          stop_multiple, target_pct, minute_dir="data/minute/options"):
    """
    Minute-first executor for options positions. See module docstring
    for the fallback behaviour and the data-quality guard.
    """
    credit = position.net_entry_credit()
    if credit <= 0:
        raise ValueError(
            f"run_lifecycle_minute only handles net-credit positions; this "
            f"position's net_entry_credit is Rs {credit}."
        )
    stop_level = -stop_multiple * credit
    target_level = target_pct * credit

    leg_chains = []
    all_ok = True
    reasons = []
    for leg in position.legs:
        chain = load_minute_chain(leg.symbol, leg.expiry, leg.strike, leg.option_type, minute_dir)
        ok, reason = minute_chain_quality(chain)
        leg_chains.append((leg, chain))
        if not ok:
            all_ok = False
            reasons.append(f"{leg.strike}{leg.option_type}: {reason}")

    path = []
    last_minute_date = None

    if not all_ok:
        path.append({"note": "minute data unavailable/insufficient for at least one leg "
                              f"({'; '.join(reasons)}) -- falling back to daily from entry_date"})
    else:
        merged = None
        for i, (leg, chain) in enumerate(leg_chains):
            c = chain[chain["timestamp"].dt.date >= pd.to_datetime(entry_date).date()].copy()
            c = c.rename(columns={"close": f"close_{i}"})
            merged = c if merged is None else pd.merge(merged, c, on="timestamp", how="inner")
        merged = merged.sort_values("timestamp").reset_index(drop=True)

        if merged.empty:
            path.append({"note": "minute chains loaded but no overlapping timestamps from "
                                  "entry_date onward -- falling back to daily"})
        else:
            for _, row in merged.iterrows():
                pnl = sum(leg.pnl(row[f"close_{i}"]) for i, (leg, _) in enumerate(leg_chains))
                pnl = round(pnl, 2)
                path.append({"timestamp": str(row["timestamp"]), "pnl": pnl, "granularity": "minute"})
                last_minute_date = row["timestamp"].date()
                if pnl <= stop_level:
                    return {"exit_timestamp": str(row["timestamp"]), "exit_reason": "STOP",
                            "pnl": pnl, "granularity": "minute", "path": path}
                if pnl >= target_level:
                    return {"exit_timestamp": str(row["timestamp"]), "exit_reason": "TARGET",
                            "pnl": pnl, "granularity": "minute", "path": path}

    fallback_start = last_minute_date.isoformat() if last_minute_date else entry_date
    return _daily_walk(conn, position, fallback_start, force_close_date, stop_level, target_level, path)


if __name__ == "__main__":
    conn = sqlite3.connect("market.db")

    print("=== E3 self-check 1: daily-only path, unchanged real regression from earlier build ===\n")
    SYMBOL, ENTRY_DATE, EXPIRY = "NIFTY", "2024-01-15", "2024-01-18"
    pos = Position(conn)
    pos.open_leg(SYMBOL, EXPIRY, 22350.0, "CE", "SELL", 1, ENTRY_DATE)
    pos.open_leg(SYMBOL, EXPIRY, 21850.0, "PE", "SELL", 1, ENTRY_DATE)
    credit = pos.net_entry_credit()
    result = run_lifecycle(conn, pos, start_date="2024-01-16", force_close_date=EXPIRY,
                            stop_multiple=2.0, target_pct=0.5)
    print(f"Exit: {result['exit_reason']} on {result['exit_date']}, P&L Rs {result['pnl']}")
    assert result["exit_reason"] == "STOP" and result["exit_date"] == "2024-01-17", \
        "REGRESSION: daily-only path no longer reproduces the known real result"
    print("Check: matches the original daily-only regression result exactly -> PASS\n")

    print("=== E3 self-check 2: minute-first path, real VRP example from prior verification ===\n")
    SYMBOL2, ENTRY2, EXPIRY2 = "NIFTY", "2026-05-25", "2026-06-02"
    pos2 = Position(conn)
    pos2.open_leg(SYMBOL2, EXPIRY2, 24500.0, "CE", "SELL", 1, ENTRY2)
    pos2.open_leg(SYMBOL2, EXPIRY2, 23600.0, "PE", "SELL", 1, ENTRY2)
    credit2 = pos2.net_entry_credit()
    print(f"Credit: Rs {credit2}, Stop: Rs {-2.0*credit2:.2f}, Target: Rs {0.5*credit2:.2f}")
    result2 = run_lifecycle_minute(conn, pos2, entry_date=ENTRY2, force_close_date=EXPIRY2,
                                    stop_multiple=2.0, target_pct=0.5)
    minute_bars = sum(1 for p in result2["path"] if p.get("granularity") == "minute")
    print(f"Real minute bars checked: {minute_bars}")
    print(f"Exit: {result2['exit_reason']} at "
          f"{result2.get('exit_timestamp', result2.get('exit_date'))}, "
          f"P&L Rs {result2['pnl']}, granularity={result2['granularity']}")
    assert result2["exit_reason"] == "TARGET" and result2["granularity"] == "minute", \
        "REGRESSION: minute-first path no longer finds a real TARGET hit via minute data"
    print("Check: real TARGET hit found via genuine minute-by-minute tracking -> PASS")
    print("(Note: this lands on a different, EARLIER timestamp than an ad-hoc manual check "
          "done earlier in this project used to report -- that check hardcoded lot_size=50; "
          "the real lot size on this date is 65 (looked up correctly here via lot_sizes), so "
          "this result is the corrected one, not a regression.)\n")

    print("=== E3 self-check 3: minute data unavailable -> clean fallback to daily, no crash ===\n")
    SYMBOL3, ENTRY3, EXPIRY3 = "NIFTY", "2026-06-03", "2026-06-09"
    pos3 = Position(conn)
    pos3.open_leg(SYMBOL3, EXPIRY3, 23900.0, "CE", "SELL", 1, ENTRY3)
    pos3.open_leg(SYMBOL3, EXPIRY3, 23050.0, "PE", "SELL", 1, ENTRY3)
    result3 = run_lifecycle_minute(conn, pos3, entry_date=ENTRY3, force_close_date=EXPIRY3,
                                    stop_multiple=2.0, target_pct=0.5)
    notes = [p["note"] for p in result3["path"] if "note" in p]
    print("Fallback note:", notes[0] if notes else "(none -- unexpected)")
    print(f"Exit: {result3['exit_reason']} on {result3.get('exit_date')}, "
          f"P&L Rs {result3['pnl']}, granularity={result3['granularity']}")
    assert notes, "REGRESSION: known-sparse 2026-06-09 file should have triggered the fallback note"
    assert result3["granularity"] == "daily", "should have fallen back to daily for this known-bad file"
    print("Check: known-sparse file correctly detected and cleanly handled, no crash -> PASS")

    conn.close()
