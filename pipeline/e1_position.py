"""
E1 -- Multi-leg position representation.

A Position is a list of Legs. Each Leg is a single option contract bought
or sold at a specific entry date/price, in a fixed number of lots. This
module handles ONLY position representation and raw (pre-cost) P&L
accounting -- entry/exit timing rules live in E3, cost-model application
happens when a caller composes this with pipeline/d_cost_model.py,
Greeks life-cycle tracking is E5, and expiry/settlement handling is E4.

Pricing convention: settlement price (`settle`), not `close`, is used
for every entry/exit/mark-to-market lookup -- this matches the
convention already used elsewhere in the pipeline (c5_iv_vectorized.py
computes IV from `settle`, not `close`), so a position's IV/delta/etc.
at any date stay consistent with the columns already in daily_options.

Lot size: looked up ONCE from lot_sizes at entry and held fixed for the
life of the trade. ASSUMPTION, logged: every strategy in this project
holds positions at most a few weeks (weekly-expiry driven), and NSE lot
size changes are announced months in advance, so no trade in this
project can actually straddle a lot-size change. If a future strategy
holds a position for months, this assumption needs revisiting.

Fails loudly, on purpose: opening a leg with no data, or marking a leg
to a date with no data, raises rather than silently returning 0 or None
-- a wrong P&L reported as a real number is worse than a crash.

Minute-level pricing (added after Task E was corrected mid-build): this
module now also exposes load_minute_chain() and minute_chain_quality(),
used by E3's minute-aware executor to price a leg intraday rather than
only once per day. The daily path above (get_option_row, mark_to_market)
is kept as-is and used as E3's fallback for any day minute data doesn't
reach -- confirmed during this build that minute coverage for a given
expiry contract stops the trading day BEFORE its own expiry (e.g. the
2026-06-02 expiry file's last real day is 2026-05-29, not 2026-06-02),
so a position held to expiry always needs at least one daily-settle
lookup for the final day, even when every earlier day has full minute
coverage.

Expiry-day settlement fix (found while building E4): daily_options'
`settle` column is NOT usable on the actual expiry date -- confirmed
across multiple real expiry dates, both symbols, that it collapses to a
single value (the underlying's own spot/futures settlement price)
identical across every strike and option_type that day, while `close`
still correctly varies per strike. get_option_row() now detects
date==expiry and substitutes the real intrinsic value (computed from
daily_spot), rather than silently pricing every option as if it were
worth the index level.
"""
import sqlite3
from dataclasses import dataclass


def get_lot_size(conn, symbol, date):
    row = conn.execute("""
        SELECT lot_size FROM lot_sizes
        WHERE symbol=? AND start_date<=? AND (end_date IS NULL OR end_date>=?)
    """, (symbol, date, date)).fetchone()
    if row is None:
        raise ValueError(f"No lot size found for {symbol} on {date}")
    return row[0]


def get_spot_close(conn, symbol, date):
    row = conn.execute("SELECT close FROM daily_spot WHERE symbol=? AND date=?", (symbol, date)).fetchone()
    return row[0] if row else None


def get_option_row(conn, symbol, date, expiry, strike, option_type):
    row = conn.execute("""
        SELECT settle, iv, delta, gamma, theta, vega, is_liquid, dte
        FROM daily_options
        WHERE symbol=? AND date=? AND expiry=? AND strike=? AND option_type=?
    """, (symbol, date, expiry, strike, option_type)).fetchone()
    if row is None:
        return None
    result = dict(zip(
        ["settle", "iv", "delta", "gamma", "theta", "vega", "is_liquid", "dte"], row
    ))
    # BUG FOUND during E4 build, fixed here at the root: on the actual expiry
    # date, `settle` in daily_options is NOT the option's own value -- it's
    # the underlying's spot/futures settlement price, identical across every
    # strike and option_type that day (confirmed across multiple real expiry
    # dates, both symbols -- `close` still varies correctly per strike, only
    # `settle` collapses). Using it as-is would silently price every option
    # as if it were worth the index level. On expiry day specifically,
    # substitute the correct intrinsic value instead.
    if date == expiry:
        spot = get_spot_close(conn, symbol, date)
        if spot is None:
            raise ValueError(
                f"Expiry-day settlement for {symbol} {expiry} {strike}{option_type} needs "
                f"the underlying's spot close on {date} (daily_spot has no row) -- refusing "
                f"to fall back to the raw `settle` column, which is known-wrong on expiry "
                f"days (it holds the underlying's price, not the option's)."
            )
        intrinsic = max(spot - strike, 0.0) if option_type == "CE" else max(strike - spot, 0.0)
        result["settle"] = intrinsic
        result["_expiry_settlement_spot"] = spot
    return result


def load_minute_chain(symbol, expiry, strike, option_type, minute_dir="data/minute/options"):
    """
    Loads one leg's own minute-level price series for its expiry contract.
    Returns a DataFrame [timestamp, close] sorted by time, or None if the
    file doesn't exist or has no rows for this strike/type. Returns None
    rather than raising -- E3 decides what to do (fall back to daily),
    since "no minute data for this leg" is an expected, routine condition
    given the known gap weeks (see roadmap C.8), not an error.
    """
    import os
    import pandas as pd
    path = f"{minute_dir}/{symbol}/{expiry}.parquet"
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    df = df[(df["strike"] == strike) & (df["option_type"] == option_type)]
    if df.empty:
        return None
    return df[["timestamp", "close"]].sort_values("timestamp").reset_index(drop=True)


def minute_chain_quality(chain_df):
    """
    Local data-availability guard -- flags the kind of near-empty file
    found during Task E development (the 32KB / 1-real-day 2026-06-09
    file). Not the full E7 centralized gate (that belongs at the
    strategy-entry level); this just stops this module from silently
    trusting a chain that clearly isn't real week-long coverage.
    Returns (ok: bool, reason: str).
    """
    if chain_df is None or len(chain_df) == 0:
        return False, "no minute data file (or no rows) for this leg"
    n_days = chain_df["timestamp"].dt.date.nunique()
    if n_days <= 1:
        return False, f"only {n_days} trading day(s) of minute data -- looks like a known-sparse file"
    return True, "ok"


@dataclass
class Leg:
    symbol: str
    expiry: str
    strike: float
    option_type: str      # 'CE' or 'PE'
    side: str              # 'BUY' or 'SELL'
    lots: int
    entry_date: str
    entry_price: float
    lot_size: int

    def __post_init__(self):
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {self.side}")
        if self.option_type not in ("CE", "PE"):
            raise ValueError(f"option_type must be CE or PE, got {self.option_type}")

    def sign(self):
        return 1 if self.side == "BUY" else -1

    def pnl(self, current_price):
        """Raw P&L in rupees for this leg at current_price, pre-cost."""
        return self.sign() * (current_price - self.entry_price) * self.lot_size * self.lots

    def entry_cashflow(self):
        """Cash received (+) or paid (-) at entry, pre-cost."""
        return -self.sign() * self.entry_price * self.lot_size * self.lots


class Position:
    """A collection of Legs opened together as one structure."""

    def __init__(self, conn):
        self.conn = conn
        self.legs = []

    def open_leg(self, symbol, expiry, strike, option_type, side, lots, entry_date):
        row = get_option_row(self.conn, symbol, entry_date, expiry, strike, option_type)
        if row is None:
            raise ValueError(
                f"No data for {symbol} {expiry} {strike}{option_type} on {entry_date} "
                f"-- cannot open a leg without a real settle price."
            )
        if row["is_liquid"] == 0:
            raise ValueError(
                f"{symbol} {expiry} {strike}{option_type} on {entry_date} fails the "
                f"liquidity filter (is_liquid=0) -- E7 should screen this out before "
                f"a strategy ever reaches here; opening it anyway would be wrong."
            )
        lot_size = get_lot_size(self.conn, symbol, entry_date)
        leg = Leg(symbol, expiry, strike, option_type, side, lots, entry_date,
                   entry_price=row["settle"], lot_size=lot_size)
        self.legs.append(leg)
        return leg

    def mark_to_market(self, date):
        """
        Returns (total_pnl, per_leg_detail) as of `date`. Raises if any
        leg has no data on `date` -- see module docstring.
        """
        total = 0.0
        detail = []
        for leg in self.legs:
            row = get_option_row(self.conn, leg.symbol, date, leg.expiry,
                                   leg.strike, leg.option_type)
            if row is None:
                raise ValueError(
                    f"No data for {leg.symbol} {leg.expiry} {leg.strike}{leg.option_type} "
                    f"on {date} -- cannot mark this leg to market."
                )
            leg_pnl = leg.pnl(row["settle"])
            total += leg_pnl
            detail.append({
                "leg": f"{leg.side} {leg.lots}x {leg.strike}{leg.option_type} (exp {leg.expiry})",
                "entry_price": leg.entry_price,
                "current_price": row["settle"],
                "pnl": round(leg_pnl, 2),
            })
        return round(total, 2), detail

    def net_entry_credit(self):
        """Net cash received (+) or paid (-) at entry, across all legs, pre-cost."""
        return round(sum(leg.entry_cashflow() for leg in self.legs), 2)


if __name__ == "__main__":
    # Self-check against REAL data -- not a synthetic example. Picks an
    # actual NIFTY weekly short strangle and shows every number so it can
    # be hand-verified against the raw daily_options rows directly.
    conn = sqlite3.connect("market.db")

    SYMBOL, ENTRY_DATE, EXPIRY = "NIFTY", "2024-01-15", "2024-01-18"
    CALL_STRIKE, PUT_STRIKE = 22300.0, 21900.0
    EXIT_DATE = "2024-01-17"

    print(f"=== E1 self-check: real {SYMBOL} short strangle, {ENTRY_DATE} -> {EXIT_DATE} ===\n")

    pos = Position(conn)
    call_leg = pos.open_leg(SYMBOL, EXPIRY, CALL_STRIKE, "CE", "SELL", 1, ENTRY_DATE)
    put_leg = pos.open_leg(SYMBOL, EXPIRY, PUT_STRIKE, "PE", "SELL", 1, ENTRY_DATE)

    print("Legs opened:")
    for leg in pos.legs:
        print(f"  {leg.side} 1x {leg.strike}{leg.option_type} @ {leg.entry_price} "
              f"(lot_size={leg.lot_size})")

    credit = pos.net_entry_credit()
    print(f"\nNet entry credit (should be positive -- we sold both legs): Rs {credit}")

    total_pnl, detail = pos.mark_to_market(EXIT_DATE)
    print(f"\nMark-to-market on {EXIT_DATE}:")
    for d in detail:
        print(f"  {d['leg']}: entry={d['entry_price']} -> now={d['current_price']}, "
              f"leg P&L=Rs {d['pnl']}")
    print(f"\nTotal position P&L: Rs {total_pnl}")

    # Hand-verifiable invariant: total must equal the sum of the two
    # independently-printed leg P&Ls above.
    manual_sum = round(sum(d["pnl"] for d in detail), 2)
    assert total_pnl == manual_sum, f"MISMATCH: total={total_pnl} vs manual sum={manual_sum}"
    print(f"\nCheck: total_pnl ({total_pnl}) == sum of leg P&Ls ({manual_sum}) -> PASS")

    conn.close()
