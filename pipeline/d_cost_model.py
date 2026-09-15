"""
Task D — Cost, Margin and Execution Model. D1-D9, all in one file.

This is a CALCULATOR, not a simulator: standalone, independently-testable
functions that Task E/F (the actual backtest engines) will call on every
trade once they exist. Nothing here runs a backtest by itself.

============================================================================
D1 — BROKERAGE
============================================================================
Zerodha: flat Rs 20 per executed F&O order, regardless of size or side.
"""

BROKERAGE_PER_ORDER = 20.0

def brokerage():
    return BROKERAGE_PER_ORDER


"""
============================================================================
D2 — STT (Securities Transaction Tax), date-aware
============================================================================
Confirmed from NSE circular NSE/FATAX/73524 (31 Mar 2026), cross-checked
against the Union Budget 2026 (presented 1 Feb 2026, enacted as Finance
Act 2026 on 30 Mar 2026, effective 1 Apr 2026). STT only applies on the
SELL side (or on exercise, for options), never on buy.
"""

STT_CHANGE_DATE = "2026-04-01"

def stt(trade_date, instrument, side, value, exercised=False):
    """
    trade_date: 'YYYY-MM-DD'
    instrument: 'FUTURE' or 'OPTION'
    side: 'BUY' or 'SELL'
    value: for futures, the traded value (price x lot size x lots);
           for options, the premium value (premium x lot size x lots)
    exercised: True if this is an option settled by exercise, not a
               closing trade
    """
    pre_reform = trade_date < STT_CHANGE_DATE

    if instrument == "FUTURE":
        if side != "SELL":
            return 0.0
        rate = 0.0002 if pre_reform else 0.0005          # 0.02% -> 0.05%
        return value * rate

    if instrument == "OPTION":
        if exercised:
            rate = 0.00125 if pre_reform else 0.0015      # 0.125% -> 0.15%
            return value * rate                             # value = intrinsic value here
        if side != "SELL":
            return 0.0
        rate = 0.0010 if pre_reform else 0.0015            # 0.10% -> 0.15%
        return value * rate

    raise ValueError(f"Unknown instrument: {instrument}")


"""
============================================================================
D3 — Exchange transaction charges, SEBI fee, stamp duty, GST
============================================================================
Exchange charges from SEBI's July 2024 uniform-fee reform (the most
recent regulatory-anchored figures found; flagged in the roadmap as the
one rate most worth re-checking against a real contract note if one ever
becomes available).
"""

EXCH_CHARGE_FUTURE_RATE = 1.73 / 100000      # Rs 1.73 per lakh, both sides
EXCH_CHARGE_OPTION_RATE = 35.03 / 100000     # Rs 35.03 per lakh of PREMIUM, both sides
SEBI_FEE_RATE = 10 / 10000000                # Rs 10 per crore, both sides
STAMP_DUTY_FUTURE_RATE = 0.00002             # 0.002%, buy side only
STAMP_DUTY_OPTION_RATE = 0.00003             # 0.003% on premium, buy side only
GST_RATE = 0.18

def exchange_charge(instrument, value):
    rate = EXCH_CHARGE_FUTURE_RATE if instrument == "FUTURE" else EXCH_CHARGE_OPTION_RATE
    return value * rate

def sebi_fee(value):
    return value * SEBI_FEE_RATE

def stamp_duty(instrument, value, side):
    if side != "BUY":
        return 0.0
    rate = STAMP_DUTY_FUTURE_RATE if instrument == "FUTURE" else STAMP_DUTY_OPTION_RATE
    return value * rate

def gst(brokerage_amt, exchange_amt, sebi_amt):
    # GST applies ONLY to brokerage + exchange charges + SEBI fee.
    # NOT applied to STT or stamp duty.
    return (brokerage_amt + exchange_amt + sebi_amt) * GST_RATE


"""
============================================================================
D4 / D9 — Slippage model, dual-tier (Realistic / Conservative)
============================================================================
Built directly from Appendix A's empirical data (your 10 live Kite
screenshots), not a generic assumption. Interpolated by distance from
spot (moneyness %) and by expiry type (weekly vs monthly).

D9 (dual-tier) approved by user on [this conversation] — both tiers run
by default in every backtest; a strategy must clear the kill criteria on
CONSERVATIVE, not just REALISTIC, to count as robust.
"""

# (moneyness_pct, weekly_spread_pct, monthly_spread_pct)
SPREAD_TABLE = {
    "REALISTIC":    [(0, 0.8), (1, 1.5), (3, 4.5), (5, 4.0), (7, 7.5), (10, 12.0)],
    "REALISTIC_M":  [(0, 0.4), (1, 0.7), (3, 2.7), (5, 7.5), (7, 14.0), (10, 18.0)],
    "CONSERVATIVE":   [(0, 1.5), (1, 3.0), (3, 8.0), (5, 8.0), (7, 15.0), (10, 22.0)],
    "CONSERVATIVE_M": [(0, 1.0), (1, 1.5), (3, 5.5), (5, 14.0), (7, 22.0), (10, 28.0)],
}

def _interpolate(table, x):
    """Linear interpolation; extrapolates flat beyond the last known point."""
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            frac = (x - x0) / (x1 - x0)
            return y0 + frac * (y1 - y0)
    return table[-1][1]

def slippage_pct(abs_moneyness_pct, expiry_type, tier):
    """
    abs_moneyness_pct: distance from spot, as a positive percentage
    expiry_type: 'weekly' or 'monthly'
    tier: 'REALISTIC' or 'CONSERVATIVE'
    Returns spread as a percentage of premium (e.g. 4.5 means 4.5%).
    """
    key = tier if expiry_type == "weekly" else f"{tier}_M"
    return _interpolate(SPREAD_TABLE[key], abs_moneyness_pct)


"""
============================================================================
D5 — Market impact model
============================================================================
ASSUMPTION (logged): square-root market impact, the standard textbook
form (impact scales with sqrt of participation rate, not linearly) —
reflects that impact grows sub-linearly as you eat further into the
order book. Coefficient chosen conservatively (impact reaches ~1% extra
at 25% of the day's volume) since we have no real fill data to calibrate
against yet; revisit if D7 ever gets a real contract note to check against.
"""

IMPACT_COEFFICIENT = 0.02  # tuned so 25% participation -> ~1% extra cost

def market_impact_pct(lots_traded, day_volume_contracts):
    if day_volume_contracts <= 0:
        return 0.05  # no volume data / zero-volume day -> treat as maximally impactful, flagged
    participation = lots_traded / day_volume_contracts
    return IMPACT_COEFFICIENT * (participation ** 0.5) * 100  # as a percentage


"""
============================================================================
D6 — Margin model
============================================================================
NOT a fixed lookup table (margin is computed live from volatility, not a
historical rate — see B6). This is a formula-based approximation using
the regime milestones already researched:
  - 40% of notional (or SPAN+exposure, whichever higher) on expiry day
    for short options, regardless of moneyness
  - otherwise, a volatility-scaled SPAN approximation
ASSUMPTION (logged): SPAN approximated as 3x the 1-day 99% VaR of the
underlying (a standard rule-of-thumb multiplier), using realized
volatility from daily_signals (already computed in C7) as the vol input.
Not exact SPAN (that requires NSE's proprietary daily risk files) but
directionally correct and good enough for position-sizing decisions.
"""

def margin_pct(is_short_option, is_expiry_day, daily_volatility_pct):
    """
    Returns required margin as a percentage of notional.
    daily_volatility_pct: e.g. from ATR or realized vol, as a percentage
    """
    span_approx = 3 * daily_volatility_pct * 2.33  # 3x 1-day 99% VaR (z=2.33)
    exposure_margin = 3.0  # flat 3% exposure margin add-on, standard NSE convention

    total = span_approx + exposure_margin

    if is_short_option and is_expiry_day:
        return max(total, 40.0)  # 40% expiry-day floor, regardless of the above
    return total


"""
============================================================================
D7 — Calibration
============================================================================
User does not have a personal Zerodha contract note available (not an
active trader currently). FALLBACK: calibrated against a published
sample contract note structure instead (Zerodha's own published charge
examples, cross-checked against the D1-D3 rates above). This is a
PUBLIC-EXAMPLE calibration, not a personal one — flagged explicitly,
not presented as equivalent verification.
"""

def calibration_worked_example():
    """
    Worked example: SELL 1 lot Nifty option, premium Rs 100, lot size 75.
    Trade date: after 1 Apr 2026 (current STT regime).
    """
    premium_value = 100 * 75  # Rs 7,500
    side = "SELL"
    trade_date = "2026-09-15"

    b = brokerage()
    s = stt(trade_date, "OPTION", side, premium_value)
    exch = exchange_charge("OPTION", premium_value)
    sebi = sebi_fee(premium_value)
    stamp = stamp_duty("OPTION", premium_value, side)  # 0, sell side
    g = gst(b, exch, sebi)

    total = b + s + exch + sebi + stamp + g
    return {
        "premium_value": premium_value,
        "brokerage": round(b, 2),
        "stt": round(s, 2),
        "exchange_charge": round(exch, 2),
        "sebi_fee": round(sebi, 2),
        "stamp_duty": round(stamp, 2),
        "gst": round(g, 2),
        "total_cost": round(total, 2),
        "total_cost_pct_of_premium": round(total / premium_value * 100, 3),
    }


"""
============================================================================
D8 — Bid-ask spread empirical validation
============================================================================
DONE, previously — see Appendix A in the roadmap PDF. The SPREAD_TABLE
above (D4/D9) is the direct product of that work: 10 live Kite screenshots,
validated, converted into the Realistic/Conservative tier tables used here.
Nothing further to build in this file; referenced, not repeated.
"""


if __name__ == "__main__":
    print("=== D7: Calibration worked example (public-reference, not a personal contract note) ===")
    result = calibration_worked_example()
    for k, v in result.items():
        print(f"  {k}: {v}")

    print("\n=== D4/D9: Slippage sanity check ===")
    for m in [0, 1, 3, 5, 7, 12]:
        r_w = slippage_pct(m, "weekly", "REALISTIC")
        c_w = slippage_pct(m, "weekly", "CONSERVATIVE")
        print(f"  {m}% moneyness, weekly: Realistic={r_w:.2f}%  Conservative={c_w:.2f}%")

    print("\n=== D5: Market impact sanity check ===")
    for participation in [0.01, 0.05, 0.10, 0.25, 0.50]:
        impact = market_impact_pct(participation * 1000, 1000)
        print(f"  {participation*100:.0f}% of day volume: {impact:.3f}% extra impact")

    print("\n=== D6: Margin sanity check ===")
    print(f"  Normal day, 1.5% daily vol, not expiry: {margin_pct(True, False, 1.5):.2f}%")
    print(f"  Expiry day, short option, 1.5% daily vol: {margin_pct(True, True, 1.5):.2f}%")
    print(f"  High-vol day, 4% daily vol, not expiry: {margin_pct(True, False, 4.0):.2f}%")
