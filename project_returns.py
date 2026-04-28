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

DAILY_PROFIT_CAP  = 5.0        # bot stops new trades after this daily profit (USD)
DAILY_LOSS_LIMIT  = 7.0        # bot stops new trades after this daily loss (USD)

NZD_USD = 0.593                # approximate NZD → USD


def nzd_to_usd(nzd: float) -> float:
    return nzd * NZD_USD


def trade_ev(balance: float, win_rate: float, rr: float, trades_per_day: int) -> float:
    """Expected value per day after fees, with compounding on position size."""
    risk    = balance * RISK_PER_TRADE
    reward  = risk * rr
    fee_per_trade = balance * POSITION_SIZE_PCT * ROUND_TRIP_FEE
    ev_per_trade  = win_rate * reward - (1 - win_rate) * risk - fee_per_trade
    return ev_per_trade * trades_per_day


def apply_daily_cap(daily_pnl: float, cap: float, limit: float) -> float:
    """Clip daily P&L to reflect the bot's profit cap and loss limit."""
    return max(-limit, min(cap, daily_pnl))


def project(start_usd: float, win_rate: float, rr: float,
            trades_per_day: int, days: int,
            apply_caps: bool = False) -> list:
    """Return list of (day, balance, day_pnl, cumulative_pnl)."""
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
    width = 66
    print(f"\n{'═' * width}")
    print(f"  {title}")
    print(f"{'═' * width}")


def fmt_usd(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}${v:.2f}"


def main():
    parser = argparse.ArgumentParser(description="Compound returns projection")
    parser.add_argument("--capital",   type=float, default=750,
                        help="Starting capital (default: 750)")
    parser.add_argument("--currency",  type=str,   default="NZD",
                        choices=["NZD", "USD"],
                        help="Currency of capital (default: NZD)")
    parser.add_argument("--winrate",   type=float, default=None,
                        help="Win rate 0-1 (default: show all scenarios)")
    parser.add_argument("--rr",        type=float, default=2.0,
                        help="Reward:Risk ratio (default: 2.0 for 1:2 R:R)")
    parser.add_argument("--trades",    type=int,   default=2,
                        help="Trades per day (default: 2)")
    parser.add_argument("--days",      type=int,   default=7,
                        help="Projection horizon in trading days (default: 7)")
    args = parser.parse_args()

    start_nzd = args.capital if args.currency == "NZD" else args.capital / NZD_USD
    start_usd = nzd_to_usd(start_nzd) if args.currency == "NZD" else args.capital

    print(f"\n{'═' * 66}")
    print(f"  BINANCEBOT — COMPOUND RETURNS PROJECTION")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 66}")
    print(f"\n  Capital:       {args.capital} {args.currency}  (≈ ${start_usd:.0f} USD at {NZD_USD} NZD/USD)")
    print(f"  R:R ratio:     1 : {args.rr:.1f}")
    print(f"  Risk/trade:    {RISK_PER_TRADE*100:.0f}% of balance  (= ${start_usd * RISK_PER_TRADE:.2f} on day 1)")
    print(f"  Fee/trade:     {ROUND_TRIP_FEE*100:.2f}% of position  (≈ ${start_usd * POSITION_SIZE_PCT * ROUND_TRIP_FEE:.2f}/trade)")
    print(f"  Trades/day:    {args.trades}")
    print(f"  Horizon:       {args.days} trading days")

    # ── Breakeven analysis ──────────────────────────────────────────────────
    section("BREAKEVEN WIN RATE")
    # Solve: wr * rr * risk - (1-wr) * risk - fee = 0
    # wr*(rr+1)*risk = risk + fee
    # wr = (risk + fee) / ((rr+1)*risk)
    risk = start_usd * RISK_PER_TRADE
    fee  = start_usd * POSITION_SIZE_PCT * ROUND_TRIP_FEE
    breakeven_wr = (risk + fee) / ((args.rr + 1) * risk)
    print(f"\n  At 1:{args.rr:.0f} R:R with fees, you need >{breakeven_wr*100:.1f}% win rate to be profitable.")
    print(f"  (Pure 1:{args.rr:.0f} without fees: {100/(args.rr+1):.1f}%)")

    # ── Scenario comparison ─────────────────────────────────────────────────
    scenarios = (
        [args.winrate] if args.winrate is not None
        else [0.35, 0.40, 0.45, 0.50]
    )

    section(f"{args.days}-DAY COMPOUNDING  (percentage-based position sizing)")
    print(f"  Note: requires adjusting POSITION_USDT_TARGET in the bot each day.\n")

    for wr in scenarios:
        rows = project(start_usd, wr, args.rr, args.trades, args.days)
        end_bal  = rows[-1][1]
        total_pnl= rows[-1][3]
        roi      = total_pnl / start_usd * 100
        nzd_end  = end_bal / NZD_USD
        nzd_pnl  = total_pnl / NZD_USD

        print(f"  ─── Win Rate {wr*100:.0f}%  ({'ABOVE' if wr > breakeven_wr else 'BELOW'} breakeven) ───")
        table_rows = [
            (f"Day {r[0]}", f"${r[1]:.2f}  ({r[1]/NZD_USD:.0f} NZD)",
             fmt_usd(r[2]), fmt_usd(r[3]))
            for r in rows
        ]
        print(tabulate(table_rows,
                       headers=["Day", "Balance (USD / NZD)", "Day P&L", "Cum. P&L"],
                       tablefmt="simple" if HAS_TABULATE else "plain"))
        print(f"\n  Week-end:  ${end_bal:.2f} USD  ({nzd_end:.0f} NZD)  |  "
              f"Gain: {fmt_usd(total_pnl)} USD ({nzd_pnl:+.0f} NZD)  |  ROI: {roi:+.1f}%\n")

    # ── Capped version (current bot config) ────────────────────────────────
    if args.winrate is None:
        section(f"{args.days}-DAY WITH CURRENT BOT CAPS  (${DAILY_PROFIT_CAP} profit / ${DAILY_LOSS_LIMIT} loss limits)")
        print(f"  ⚠  Bot stops trading after ${DAILY_PROFIT_CAP}/day profit OR ${DAILY_LOSS_LIMIT}/day loss.\n")

        for wr in [0.40, 0.45, 0.50]:
            rows = project(start_usd, wr, args.rr, args.trades, args.days, apply_caps=True)
            end_bal   = rows[-1][1]
            total_pnl = rows[-1][3]
            roi       = total_pnl / start_usd * 100
            print(f"  Win rate {wr*100:.0f}%:  ${end_bal:.2f} USD  |  {fmt_usd(total_pnl)}  ({roi:+.1f}%)")

        print(f"\n  To unlock full compounding, scale the daily limits with account size:")
        cap_pct   = DAILY_PROFIT_CAP  / start_usd * 100
        limit_pct = DAILY_LOSS_LIMIT / start_usd * 100
        print(f"    Current ${start_usd:.0f} account → profit target = {cap_pct:.2f}% of account")
        print(f"    Suggestion: set DAILY_PROFIT_TARGET = account × {cap_pct:.3f}")
        print(f"                set MAX_DAILY_LOSS      = account × {limit_pct:.3f}")

    # ── Config recommendations ───────────────────────────────────────────────
    section("BOT CONFIG RECOMMENDATIONS FOR 750 NZD")
    risk_usd = start_usd * RISK_PER_TRADE
    pos_usd  = start_usd * POSITION_SIZE_PCT
    print(f"""
  Based on {start_nzd:.0f} NZD (≈${start_usd:.0f} USD):

    POSITION_USDT_TARGET = {pos_usd:.0f}   (was 60 — scaled to 12% of ${start_usd:.0f})
    POSITION_USDT_MIN    = {pos_usd * 0.9:.0f}
    POSITION_USDT_MAX    = {pos_usd * 1.1:.0f}
    DAILY_PROFIT_TARGET  = {DAILY_PROFIT_CAP / 450 * start_usd:.2f}  (scaled from $5 on $450)
    MAX_DAILY_LOSS       = {DAILY_LOSS_LIMIT / 450 * start_usd:.2f}  (scaled from $7 on $450)

  These values scale the bot's risk proportionally to your capital.
  Update them in python-bot/smart_trader_v3_live.py lines 47-66.
""")

    print(f"{'═' * 66}\n")


if __name__ == "__main__":
    main()
