"""
E5 -- Margin and Greeks tracking, through the life of a trade.

Greeks: two lookup paths now exist.
- position_greeks() -- daily, from daily_options (delta/gamma/theta/vega,
  Task C6). Unchanged, still the right tool for a once-per-day snapshot.
- position_greeks_minute() -- NEW. Minute-level Greeks now exist
  (pipeline/c6_minute_greeks.py, 101.6M rows across minute_greeks_YYYY.db,
  one file per year, delivered separately from market.db given their
  size). This function ATTACHes the correct year's file for the
  requested timestamp, queries it, detaches. Falls back to None with a
  clear reason if that year's file isn't present locally -- these are
  large per-year files, not guaranteed to always be downloaded/attached
  in every environment this code runs in.

Margin: uses D6's margin_pct(is_short_option, is_expiry_day,
daily_volatility_pct) from pipeline/d_cost_model.py, applied only to
SHORT legs -- a long option's maximum loss is the premium already paid
(tracked via Leg.entry_cashflow in E1), so it needs no separate margin.
Margin is charged on NOTIONAL (strike x lot_size x lots), the standard
convention for index-option margining, not on premium.

daily_volatility_pct judgment call: D6's formula needs a volatility
input but daily_signals (C7) has no pre-computed rolling-vol column,
only ret_1d (a single day's return) and atr14 (absolute points, not a
percentage). Built a trailing 20-trading-day realized volatility here
(stdev of ret_1d over the prior 20 sessions, as a %) -- a standard,
defensible window for a "1-day 99% VaR" style calculation, consistent
with D6's own stated approximation. Logged as a judgment call, not a
re-litigation of D6 itself.
"""
import sqlite3
import os
import pandas as pd
from d_cost_model import margin_pct


def trailing_volatility_pct(conn, symbol, date, window=20):
    """Trailing `window`-session stdev of daily returns, as a percentage."""
    rows = conn.execute("""
        SELECT ret_1d FROM daily_signals
        WHERE symbol=? AND date<=? AND ret_1d IS NOT NULL
        ORDER BY date DESC LIMIT ?
    """, (symbol, date, window)).fetchall()
    if len(rows) < 5:
        raise ValueError(f"Not enough trailing return history for {symbol} as of {date} "
                          f"to estimate volatility (found {len(rows)} rows, need >=5).")
    rets = pd.Series([r[0] for r in rows])
    return float(rets.std() * 100)


def position_greeks(conn, position, date):
    """Aggregate delta/gamma/theta/vega across all legs, as of `date`. Daily only."""
    totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    missing = []
    for leg in position.legs:
        row = conn.execute("""
            SELECT delta, gamma, theta, vega FROM daily_options
            WHERE symbol=? AND date=? AND expiry=? AND strike=? AND option_type=?
        """, (leg.symbol, date, leg.expiry, leg.strike, leg.option_type)).fetchone()
        if row is None or row[0] is None:
            missing.append(f"{leg.strike}{leg.option_type}")
            continue
        sign = leg.sign()
        for key, val in zip(["delta", "gamma", "theta", "vega"], row):
            totals[key] += sign * val * leg.lots
    for k in totals:
        totals[k] = round(totals[k], 4)
    return totals, missing


def position_greeks_minute(conn, position, timestamp, minute_greeks_dir="."):
    """
    Minute-level Greeks for a position at an exact timestamp (e.g.
    '2026-05-25 10:49:00+05:30'). Attaches the year-specific
    minute_greeks_YYYY.db file for the timestamp's year, queries it,
    detaches. Returns (totals_dict_or_None, missing_legs, note) -- note
    explains why totals is None if that year's file isn't available
    locally, rather than silently returning zeros.
    """
    year = pd.to_datetime(timestamp).year
    db_file = os.path.join(minute_greeks_dir, f"minute_greeks_{year}.db")
    if not os.path.exists(db_file):
        return None, [], (f"minute_greeks_{year}.db not present in {minute_greeks_dir} -- "
                           f"download/reassemble it first (see Appendix C.6)")

    conn.execute("ATTACH DATABASE ? AS mg", (db_file,))
    try:
        totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        missing = []
        for leg in position.legs:
            row = conn.execute("""
                SELECT delta, gamma, theta, vega FROM mg.minute_greeks
                WHERE symbol=? AND expiry=? AND timestamp=? AND strike=? AND option_type=?
            """, (leg.symbol, leg.expiry, timestamp, leg.strike, leg.option_type)).fetchone()
            if row is None:
                missing.append(f"{leg.strike}{leg.option_type}")
                continue
            sign = leg.sign()
            for key, val in zip(["delta", "gamma", "theta", "vega"], row):
                totals[key] += sign * val * leg.lots
        for k in totals:
            totals[k] = round(totals[k], 4)
        return totals, missing, "ok"
    finally:
        conn.execute("DETACH DATABASE mg")


def position_margin(conn, position, date, force_close_date=None):
    """
    Total margin requirement (Rs) across all SHORT legs, as of `date`.
    is_expiry_day is True when `date` is the force_close_date (assumed
    to be the expiry, matching every strategy spec in the roadmap).
    """
    symbol = position.legs[0].symbol
    vol = trailing_volatility_pct(conn, symbol, date)
    is_expiry_day = (force_close_date is not None and date == force_close_date)

    total_margin = 0.0
    detail = []
    for leg in position.legs:
        if leg.side != "SELL":
            continue  # long legs: max loss is the premium already paid, no separate margin
        notional = leg.strike * leg.lot_size * leg.lots
        pct = margin_pct(is_short_option=True, is_expiry_day=is_expiry_day, daily_volatility_pct=vol)
        margin_rs = notional * pct / 100
        total_margin += margin_rs
        detail.append({"leg": f"SELL {leg.lots}x {leg.strike}{leg.option_type}",
                        "notional": notional, "margin_pct": round(pct, 2), "margin_rs": round(margin_rs, 2)})
    return round(total_margin, 2), detail, vol


if __name__ == "__main__":
    conn = sqlite3.connect("market.db")
    import sys
    sys.path.insert(0, ".")
    from e1_position import Position

    print("=== E5 self-check: real short strangle, Greeks and margin on a real date ===\n")
    SYMBOL, ENTRY, EXPIRY = "NIFTY", "2024-01-15", "2024-01-18"
    pos = Position(conn)
    pos.open_leg(SYMBOL, EXPIRY, 22300.0, "CE", "SELL", 1, ENTRY)
    pos.open_leg(SYMBOL, EXPIRY, 21900.0, "PE", "SELL", 1, ENTRY)

    greeks, missing = position_greeks(conn, pos, ENTRY)
    print(f"Position Greeks on {ENTRY}: {greeks}")
    assert not missing, f"Unexpected missing Greeks for: {missing}"
    # Hand-checkable sanity: two SELL legs (call above spot, put below spot) ->
    # net delta should be small (roughly offsetting), theta should be positive
    # (short premium collects time decay), gamma/vega should be negative
    # (short options have negative gamma and vega).
    assert greeks["theta"] > 0, "short strangle should show positive theta (collecting decay)"
    assert greeks["gamma"] < 0, "short strangle should show negative gamma"
    assert greeks["vega"] < 0, "short strangle should show negative vega"
    print("Check: signs match real short-strangle risk profile (theta>0, gamma<0, vega<0) -> PASS\n")

    margin, detail, vol = position_margin(conn, pos, ENTRY, force_close_date=EXPIRY)
    print(f"Trailing 20-day realized vol on {ENTRY}: {vol:.2f}%")
    for d in detail:
        print(f"  {d['leg']}: notional Rs {d['notional']:,.0f}, margin {d['margin_pct']}% = Rs {d['margin_rs']:,.2f}")
    print(f"Total margin required (entry day): Rs {margin:,.2f}")

    margin_exp, detail_exp, _ = position_margin(conn, pos, EXPIRY, force_close_date=EXPIRY)
    print(f"\nTotal margin required (expiry day, should hit the 40% floor): Rs {margin_exp:,.2f}")
    expected_floor = sum(leg.strike * leg.lot_size * leg.lots for leg in pos.legs if leg.side == "SELL") * 0.40
    assert abs(margin_exp - expected_floor) < 1.0, \
        f"Expiry-day margin ({margin_exp}) doesn't match the 40% notional floor ({expected_floor})"
    print(f"Check: expiry-day margin matches the 40% notional floor exactly (Rs {expected_floor:,.2f}) -> PASS")

    conn.close()
