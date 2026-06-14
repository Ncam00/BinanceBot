#!/usr/bin/env python3
"""
Compound returns projector — shows expected daily/weekly P&L for given capital at 1:2 R:R.
Run: python project_returns.py [--capital 750] [--currency NZD] [--winrate 0.45] [--days 7]
"""
import argparse
import math
from datetime import datetime

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False
    def tabulate(rows, headers=(), tablefmt="", floatfmt="", **_):
        lines = ["  ".join(str(h) for h in headers)]
        for row in rows:
            lines.append("  ".join(str(c) for c in row))
        return "\n".join(lines)

# Bot constants (from smart_trader_v3_live.py)
RISK_PER_TRADE    = 0.01       # 1% of balance
POSITION_SIZE_PCT = 0.12       # 12% of balance per trade
FEE_RATE          = 0.001      # 0.1% per side
SLIPPAGE_RATE     = 0.0005     # 0.05%
ROUND_TRIP_FEE    = FEE_RATE * 2 + SLIPPAGE_RATE   # 0.0025

# Updated: profit cap raised to $7 to match loss limit (was $5)
DAILY_PROFIT_CAP  = 7.0
DAILY_LOSS_LIMIT  = 7.0

NZD_USD = 0.593  # approximate NZD → USD

# ── What the smooth-mode changes did to each input ───────────────────────────
# Before smooth mode: premature exits (3-candle kill, 4-candle hard timeout,
# 1% pre-TP1 trailing stop) meant winners were cut far short of their targets.
# Estimated effective R:R before fixes: ~0.7:1
# Estimated effective R:R after fixes:  ~1.5:1 (conservative) / ~2.5:1 (if runner runs)
# Trades/day before: ~2  (B+/SCOUT included)
# Trades/day after:  ~1.5 (A+ only)
BEFORE_EFFECTIVE_RR    = 0.7
BEFORE_TRADES_PER_DAY  = 2.0
AFTER_EFFECTIVE_RR_LO  = 1.5   # conservative (runner exits quickly)
AFTER_EFFECTIVE_RR_HI  = 2.5   # optimistic (runner runs to 3R+)
AFTER_TRADES_PER_DAY   = 1.5


def nzd_to_usd(nzd: float) -> float:
    return nzd * NZD_USD


def breakeven_wr(rr: float, balance: float) -> float:
    risk = balance * RISK_PER_TRADE
    fee  = balance * POSITION_SIZE_PCT * ROUND_TRIP_FEE
    return (risk + fee) / ((rr + 1) * risk)


def trade_ev(balance: float, win_rate: float, rr: float, trades_per_day: float) -> float:
    risk          = balance * RISK_PER_TRADE
    reward        = risk * rr
    fee_per_trade = balance * POSITION_SIZE_PCT * ROUND_TRIP_FEE
    ev_per_trade  = win_rate * reward - (1 - win_rate) * risk - fee_per_trade
    return ev_per_trade * trades_per_day


def apply_daily_cap(daily_pnl: float, cap: float, limit: float) -> float:
    return max(-limit, min(cap, daily_pnl))


def project(start_usd: float, win_rate: float, rr: float,
            trades_per_day: float, days: int,
            apply_caps: bool = False) -> list:
    balance = start_usd
    rows = [(0, balance, 0.0, 0.0)]
    for day in range(1, days + 1):
        day_pnl = trade_ev(balance, win_rate, rr, trades_per_day)
        if apply_caps:
            day_pnl = apply_daily_cap(day_pnl, DAILY_PROFIT_CAP, DAILY_LOSS_LIMIT)
        balance += day_pnl
        rows.append((day, balance, day_pnl, balance - start_usd))
    return rows


def section(title: str):
    print(f"\n{'═' * 66}")
    print(f"  {title}")
    print(f"{'═' * 66}")


def fmt_usd(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}${v:.2f}"


def fmt_nzd(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{v/NZD_USD:.0f} NZD"


def main():
    parser = argparse.ArgumentParser(description="Compound returns projection")
    parser.add_argument("--capital",  type=float, default=750,
                        help="Starting capital (default: 750)")
    parser.add_argument("--currency", type=str,   default="NZD",
                        choices=["NZD", "USD"],
                        help="Currency of capital (default: NZD)")
    parser.add_argument("--winrate",  type=float, default=None,
                        help="Win rate 0-1 (default: show scenarios 40-55%%)")
    parser.add_argument("--rr",       type=float, default=None,
                        help="Override effective R:R (skips before/after comparison)")
    parser.add_argument("--trades",   type=float, default=None,
                        help="Override trades per day")
    parser.add_argument("--days",     type=int,   default=7,
                        help="Projection horizon in trading days (default: 7)")
    args = parser.parse_args()

    start_nzd = args.capital if args.currency == "NZD" else args.capital / NZD_USD
    start_usd = nzd_to_usd(start_nzd) if args.currency == "NZD" else args.capital

    print(f"\n{'═' * 66}")
    print(f"  BINANCEBOT — COMPOUND RETURNS PROJECTION")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 66}")
    print(f"\n  Capital:      {args.capital:.0f} {args.currency}  (≈ ${start_usd:.0f} USD at {NZD_USD} NZD/USD)")
    print(f"  Risk/trade:   {RISK_PER_TRADE*100:.0f}% of balance  (= ${start_usd * RISK_PER_TRADE:.2f} on day 1)")
    print(f"  Fee/trade:    {ROUND_TRIP_FEE*100:.2f}% of position  (≈ ${start_usd * POSITION_SIZE_PCT * ROUND_TRIP_FEE:.2f})")
    print(f"  Horizon:      {args.days} trading days")

    # ── Smooth-mode impact summary ──────────────────────────────────────────
    if args.rr is None:
        section("WHAT THE SMOOTH-MODE CHANGES DID TO YOUR PROJECTIONS")

        be_before = breakeven_wr(BEFORE_EFFECTIVE_RR,   start_usd)
        be_after_lo = breakeven_wr(AFTER_EFFECTIVE_RR_LO, start_usd)
        be_after_hi = breakeven_wr(AFTER_EFFECTIVE_RR_HI, start_usd)

        print(f"""
  The changes shift three inputs simultaneously:

  ┌─────────────────────┬──────────────────┬───────────────────────────────┐
  │                     │  BEFORE          │  AFTER                        │
  ├─────────────────────┼──────────────────┼───────────────────────────────┤
  │ Trades / day        │  ~{BEFORE_TRADES_PER_DAY:.0f}             │  ~{AFTER_TRADES_PER_DAY:.1f} (A+ only)               │
  │ Effective R:R       │  ~{BEFORE_EFFECTIVE_RR:.1f}:1           │  ~{AFTER_EFFECTIVE_RR_LO:.1f}:1 → ~{AFTER_EFFECTIVE_RR_HI:.1f}:1 (runner)      │
  │ Daily profit cap    │  $5.00           │  $7.00 (matches loss limit)   │
  │ Breakeven win rate  │  ~{be_before*100:.0f}%          │  ~{be_after_lo*100:.0f}% → ~{be_after_hi*100:.0f}% (conservative→opt.) │
  └─────────────────────┴──────────────────┴───────────────────────────────┘

  KEY INSIGHT: Before the fixes, the premature exits (60-min hard
  timeout, 1% pre-TP1 trail, 45-min kill) meant winners averaged
  only ~0.7R. That requires a {be_before*100:.0f}%+ win rate just to break even —
  almost impossible in real markets. With the fixes, you only need
  a {be_after_lo*100:.0f}–{be_after_hi*100:.0f}% win rate to be profitable.""")

        # Side-by-side summary at 45% win rate
        section(f"BEFORE vs AFTER  (at 45% win rate, {args.days} days, {args.capital:.0f} {args.currency})")
        scenarios_compare = [
            ("Before  (2/day, 0.7 R:R)", BEFORE_TRADES_PER_DAY, BEFORE_EFFECTIVE_RR),
            ("After — conservative (1.5/day, 1.5 R:R)", AFTER_TRADES_PER_DAY, AFTER_EFFECTIVE_RR_LO),
            ("After — optimistic   (1.5/day, 2.5 R:R)", AFTER_TRADES_PER_DAY, AFTER_EFFECTIVE_RR_HI),
        ]
        compare_rows = []
        for label, tpd, rr in scenarios_compare:
            rows = project(start_usd, 0.45, rr, tpd, args.days)
            end_bal   = rows[-1][1]
            total_pnl = rows[-1][3]
            roi       = total_pnl / start_usd * 100
            be        = breakeven_wr(rr, start_usd)
            above     = "✅" if 0.45 > be else "❌"
            compare_rows.append([
                label, f"{tpd:.1f}", f"1:{rr}",
                fmt_usd(total_pnl), f"${end_bal:.0f}  ({end_bal/NZD_USD:.0f} NZD)",
                f"{roi:+.1f}%", f"{above} (need {be*100:.0f}%)"
            ])
        print()
        print(tabulate(compare_rows,
                       headers=["Mode", "Trades/d", "R:R", "7-Day Gain", "End Balance", "ROI", "Profitable?"],
                       tablefmt="simple" if HAS_TABULATE else "plain"))

    # ── After smooth mode: full day-by-day projection ───────────────────────
    rr_to_use     = args.rr     if args.rr     is not None else AFTER_EFFECTIVE_RR_LO
    tpd_to_use    = args.trades if args.trades is not None else AFTER_TRADES_PER_DAY
    win_scenarios = [args.winrate] if args.winrate is not None else [0.40, 0.45, 0.50, 0.55]

    label = "AFTER SMOOTH MODE" if args.rr is None else f"CUSTOM  (R:R {rr_to_use}, {tpd_to_use}/day)"
    section(f"{args.days}-DAY COMPOUNDING — {label}  (conservative effective R:R {rr_to_use})")
    print(f"  {tpd_to_use:.1f} trades/day | 1:{rr_to_use} effective R:R | {RISK_PER_TRADE*100:.0f}% risk/trade\n")

    be = breakeven_wr(rr_to_use, start_usd)
    print(f"  Breakeven win rate at 1:{rr_to_use} R:R with fees: {be*100:.1f}%\n")

    for wr in win_scenarios:
        rows = project(start_usd, wr, rr_to_use, tpd_to_use, args.days)
        end_bal   = rows[-1][1]
        total_pnl = rows[-1][3]
        roi       = total_pnl / start_usd * 100
        above     = "ABOVE" if wr > be else "BELOW"

        print(f"  ─── Win Rate {wr*100:.0f}%  ({above} breakeven) ───")
        table_rows = [
            (f"Day {r[0]}", f"${r[1]:.2f}  ({r[1]/NZD_USD:.0f} NZD)",
             fmt_usd(r[2]), fmt_usd(r[3]))
            for r in rows
        ]
        print(tabulate(table_rows,
                       headers=["Day", "Balance (USD / NZD)", "Day P&L", "Cum. P&L"],
                       tablefmt="simple" if HAS_TABULATE else "plain"))
        print(f"\n  Week-end: ${end_bal:.2f}  ({end_bal/NZD_USD:.0f} NZD)  |  "
              f"Gain: {fmt_usd(total_pnl)} ({fmt_nzd(total_pnl)})  |  ROI: {roi:+.1f}%\n")

    # ── Optimistic scenario (runner runs well) ───────────────────────────────
    if args.rr is None:
        section(f"{args.days}-DAY COMPOUNDING — AFTER SMOOTH MODE  (optimistic R:R {AFTER_EFFECTIVE_RR_HI})")
        print(f"  Assumes runner reaches 3R+ before trailing stop. Less certain but possible.\n")
        be_hi = breakeven_wr(AFTER_EFFECTIVE_RR_HI, start_usd)
        print(f"  Breakeven win rate at 1:{AFTER_EFFECTIVE_RR_HI} R:R with fees: {be_hi*100:.1f}%\n")

        summary_rows = []
        for wr in win_scenarios:
            rows = project(start_usd, wr, AFTER_EFFECTIVE_RR_HI, AFTER_TRADES_PER_DAY, args.days)
            end_bal   = rows[-1][1]
            total_pnl = rows[-1][3]
            roi       = total_pnl / start_usd * 100
            summary_rows.append([
                f"{wr*100:.0f}%", fmt_usd(total_pnl), fmt_nzd(total_pnl),
                f"${end_bal:.0f}  ({end_bal/NZD_USD:.0f} NZD)", f"{roi:+.1f}%"
            ])
        print(tabulate(summary_rows,
                       headers=["Win Rate", "7-Day Gain", "In NZD", "End Balance", "ROI"],
                       tablefmt="simple" if HAS_TABULATE else "plain"))

    # ── Config recommendations ───────────────────────────────────────────────
    section("BOT CONFIG FOR 750 NZD  (python-bot/smart_trader_v3_live.py lines 47-66)")
    pos_usd = start_usd * POSITION_SIZE_PCT
    print(f"""
  Based on {start_nzd:.0f} NZD (≈${start_usd:.0f} USD):

    POSITION_USDT_TARGET = {pos_usd:.0f}   (12% of ${start_usd:.0f} — was fixed at $60)
    POSITION_USDT_MIN    = {pos_usd*0.9:.0f}
    POSITION_USDT_MAX    = {pos_usd*1.1:.0f}
    DAILY_PROFIT_TARGET  = {DAILY_PROFIT_CAP:.2f}  ✅ already updated
    MAX_DAILY_LOSS       = {DAILY_LOSS_LIMIT:.2f}  ✅ already updated

  For true compounding: update POSITION_USDT_TARGET each week as your
  balance grows. The daily limits are now equal ($7/$7).
""")

    print(f"{'═' * 66}\n")


if __name__ == "__main__":
    main()
