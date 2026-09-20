"""
E4 -- Expiry and settlement.

NIFTY and BANKNIFTY index options are European-style and CASH-settled
only at expiry -- there is no physical delivery and no early-assignment
risk the way American-style single-stock options have. This is a real
simplification worth stating explicitly: a short option position in
this project can NEVER be assigned early: it is either closed by the
strategy's own stop/target/force-close logic before expiry (E3), or it
runs to expiry and is cash-settled against intrinsic value (this
module). There is no third case to model.

This module's real job turned out to be smaller than the roadmap's
description suggested, and finding out why is the actual E4 finding:
while verifying real settlement values against real spot closes, found
that daily_options' `settle` column is corrupted on the actual expiry
date (collapses to the underlying's spot/futures price for every single
strike -- see e1_position.py's docstring and get_option_row() for the
full detail and the fix). That fix was applied at the root in E1, so
Position.mark_to_market() already returns correct settlement P&L when
called on the expiry date -- this module exists to make that
settlement logic explicit, independently verifiable, and reusable
outside of a Position (e.g. for Section 4's benchmark, which needs raw
settlement values without a live Position object).
"""
import sqlite3
from e1_position import Position, get_spot_close


def intrinsic_value(spot, strike, option_type):
    if option_type == "CE":
        return max(spot - strike, 0.0)
    elif option_type == "PE":
        return max(strike - spot, 0.0)
    raise ValueError(f"option_type must be CE or PE, got {option_type}")


def settle_position_at_expiry(conn, position):
    """
    Independent settlement check for a position exactly on its own
    expiry date -- computes intrinsic value directly from the real spot
    close, without going through Position.mark_to_market(), as a
    cross-check that the two paths agree.
    """
    expiry = position.legs[0].expiry
    symbol = position.legs[0].symbol
    spot = get_spot_close(conn, symbol, expiry)
    if spot is None:
        raise ValueError(f"No spot close for {symbol} on {expiry} -- cannot settle.")

    total = 0.0
    detail = []
    for leg in position.legs:
        iv = intrinsic_value(spot, leg.strike, leg.option_type)
        pnl = leg.pnl(iv)
        total += pnl
        detail.append({
            "leg": f"{leg.side} {leg.lots}x {leg.strike}{leg.option_type}",
            "intrinsic_value": iv, "pnl": round(pnl, 2),
        })
    return round(total, 2), detail, spot


if __name__ == "__main__":
    conn = sqlite3.connect("market.db")

    print("=== E4 self-check: independent settlement path vs Position.mark_to_market, real expiry ===\n")
    SYMBOL, ENTRY, EXPIRY = "NIFTY", "2024-01-15", "2024-01-18"
    pos = Position(conn)
    pos.open_leg(SYMBOL, EXPIRY, 22300.0, "CE", "SELL", 1, ENTRY)
    pos.open_leg(SYMBOL, EXPIRY, 21900.0, "PE", "SELL", 1, ENTRY)

    pnl_a, detail_a, spot = settle_position_at_expiry(conn, pos)
    print(f"Real spot close on {EXPIRY}: {spot}")
    for d in detail_a:
        print(f"  {d['leg']}: intrinsic {d['intrinsic_value']}, P&L Rs {d['pnl']}")
    print(f"E4 independent settlement total: Rs {pnl_a}")

    pnl_b, detail_b = pos.mark_to_market(EXPIRY)
    print(f"\nE1 mark_to_market on the same date: Rs {pnl_b}")

    assert pnl_a == pnl_b, f"MISMATCH: E4 settlement ({pnl_a}) != E1 mark_to_market ({pnl_b})"
    print(f"\nCheck: two independent code paths agree exactly ({pnl_a} == {pnl_b}) -> PASS")

    print("\nNo assignment-risk modeling needed: European cash-settled index options settle "
          "only via this path, at expiry, or are already closed by E3 before reaching it.")

    conn.close()
