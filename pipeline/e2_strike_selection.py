"""
E2 -- Strike selection by delta.

Given a date, an already-chosen expiry (expiry selection is E6's job, not
this module's), an option type, and a target delta MAGNITUDE, resolve to
the single actual tradeable strike whose delta is closest to that target.

Convention, logged: target_delta is always passed as a positive
magnitude (e.g. 0.20 for "20-delta"), regardless of option_type -- this
matches how every strategy spec in the roadmap phrases it ("20-delta
call", "10-delta put"), so callers never have to remember a sign
convention. Internally this compares the target against abs(delta),
since PE deltas are negative in the data.

Only rows with is_liquid=1 AND delta IS NOT NULL are eligible. Real data
check (2024-01-15, NIFTY, 2024-01-18 expiry): several liquid strikes
(20000, 20050, 20800) have delta=None because IV failed to converge for
that row (C5's ~84% convergence rate, not a bug) -- those must be
skipped, not treated as delta=0, or a target near them would silently
resolve to the wrong strike.

No monotonicity assumption: real per-row IV solves have enough numerical
noise near deep ITM that delta isn't perfectly monotonic in strike (e.g.
20900's delta was HIGHER than 21000's on a real date checked). This does
a plain linear scan for the closest match, not a binary search.

Tie-break, logged: on an exact distance tie (rare with real floats),
the lower strike wins -- deterministic, simple, and stated here rather
than left to dict/sort-order luck.
"""
import sqlite3


def select_strike_by_delta(conn, symbol, date, expiry, option_type, target_delta):
    """
    Returns a dict with the resolved strike, its actual delta, settle
    price, and the distance from target -- or None if no eligible row
    exists at all (e.g. every strike illiquid or IV-unconverged that day).
    """
    if target_delta < 0:
        raise ValueError(
            f"target_delta must be a positive magnitude (e.g. 0.20 for "
            f"20-delta), got {target_delta}. Sign is inferred from "
            f"option_type, not from the sign of this argument."
        )

    rows = conn.execute("""
        SELECT strike, delta, settle FROM daily_options
        WHERE symbol=? AND date=? AND expiry=? AND option_type=?
          AND is_liquid=1 AND delta IS NOT NULL
    """, (symbol, date, expiry, option_type)).fetchall()

    if not rows:
        return None

    best = None
    best_dist = None
    for strike, delta, settle in rows:
        dist = abs(abs(delta) - target_delta)
        if best is None or dist < best_dist or (dist == best_dist and strike < best[0]):
            best = (strike, delta, settle)
            best_dist = dist

    strike, delta, settle = best
    return {
        "strike": strike,
        "delta": delta,
        "settle": settle,
        "target_delta": target_delta,
        "distance": round(best_dist, 4),
    }


if __name__ == "__main__":
    conn = sqlite3.connect("market.db")
    SYMBOL, DATE, EXPIRY = "NIFTY", "2024-01-15", "2024-01-18"

    print(f"=== E2 self-check: {SYMBOL} {DATE}, expiry {EXPIRY} ===\n")

    # Case 1: ordinary 20-delta call. Hand-checked against the raw chain
    # dumped separately: 22350 (delta 0.1759, dist 0.0241) is actually
    # closer to 0.20 than 22300 (delta 0.2332, dist 0.0332) -- this is
    # exactly the kind of thing E2 exists to get right rather than eyeball.
    r = select_strike_by_delta(conn, SYMBOL, DATE, EXPIRY, "CE", 0.20)
    print(f"20-delta CE -> strike {r['strike']}, actual delta {r['delta']:.4f}, "
          f"settle {r['settle']}, distance {r['distance']}")
    assert r["strike"] == 22350.0, f"expected 22350, got {r['strike']}"
    print("  Check: matches manual chain inspection -> PASS")

    # Case 2: 20-delta put (target passed as positive magnitude even
    # though the underlying data has negative deltas for puts).
    r = select_strike_by_delta(conn, SYMBOL, DATE, EXPIRY, "PE", 0.20)
    print(f"\n20-delta PE -> strike {r['strike']}, actual delta {r['delta']:.4f}, "
          f"settle {r['settle']}, distance {r['distance']}")
    assert r["delta"] < 0, "put delta should be negative in the underlying data"
    print("  Check: sign handled correctly (negative delta, positive target) -> PASS")

    # Case 3: deep-ITM target (0.95) deliberately near the known
    # delta=None gap at strike 20800 -- proves the None-skipping logic
    # actually gets exercised, not just present in the code.
    r = select_strike_by_delta(conn, SYMBOL, DATE, EXPIRY, "CE", 0.95)
    print(f"\n95-delta CE (near the known delta=None gap at 20800) -> "
          f"strike {r['strike']}, delta {r['delta']:.4f}")
    assert r["strike"] != 20800.0, "should never resolve to a delta=None strike"
    print(f"  Check: correctly skipped the None-delta strike at 20800 -> PASS")

    conn.close()
