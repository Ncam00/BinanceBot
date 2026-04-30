"""
project_returns.py — Compound returns projection
Shows day-by-day compounding table for current balance across win-rate scenarios.
Run: python project_returns.py
"""

import os
import json
from datetime import datetime

TRADE_LOG = os.path.join(os.path.dirname(__file__), "trade_log.jsonl")

# ==============================
# CONFIG
# ==============================
STARTING_BALANCE_NZD = 750.0
NZD_TO_USD = 0.595          # approximate — update as needed
POSITION_SIZE_PCT = 0.10
ATR_SL_MULT = 1.5
ATR_TP_MULT = 3.0
TRADES_PER_DAY = 2
DAYS = 7
FEE_RATE = 0.0025           # 0.25% round-trip

# Derived
RISK_PER_TRADE_PCT = POSITION_SIZE_PCT * ATR_SL_MULT   # ~15% of position = 1.5% balance
REWARD_PER_TRADE_PCT = POSITION_SIZE_PCT * ATR_TP_MULT  # ~30% of position = 3.0% balance
WIN_RATES = [0.35, 0.40, 0.45, 0.50]


def project(balance_nzd, win_rate, days, trades_per_day):
    balance = balance_nzd
    for _ in range(days * trades_per_day):
        trade_value = balance * POSITION_SIZE_PCT
        fee = trade_value * FEE_RATE
        import random
        # Deterministic expected value calculation (not random)
        ev = (win_rate * balance * REWARD_PER_TRADE_PCT) - ((1 - win_rate) * balance * RISK_PER_TRADE_PCT) - fee
        balance += ev
    return balance


def project_table():
    starting_usd = STARTING_BALANCE_NZD * NZD_TO_USD

    print("=" * 60)
    print("  PROJECT RETURNS — Smart Trader V3")
    print(f"  Starting: {STARTING_BALANCE_NZD:.0f} NZD  |  R:R {ATR_SL_MULT}:{ATR_TP_MULT}  |  {TRADES_PER_DAY} trades/day")
    print("=" * 60)

    header = f"{'Win Rate':<12}{'7-Day Gain':>14}{'End Balance':>14}"
    print(f"\n{header}")
    print("-" * 42)

    for wr in WIN_RATES:
        end_nzd = project(STARTING_BALANCE_NZD, wr, DAYS, TRADES_PER_DAY)
        gain_usd = (end_nzd - STARTING_BALANCE_NZD) * NZD_TO_USD
        sign = "+" if gain_usd >= 0 else ""
        print(f"  {wr:.0%:<10}  {sign}${gain_usd:.2f} USD{end_nzd:>10.0f} NZD")

    print()

    # Day-by-day breakdown for 40% win rate
    print("  Day-by-day at 40% win rate:")
    print(f"  {'Day':<6}{'Balance (NZD)':>14}{'Daily Gain (USD)':>18}")
    print("  " + "-" * 38)
    balance = STARTING_BALANCE_NZD
    for day in range(1, DAYS + 1):
        prev = balance
        for _ in range(TRADES_PER_DAY):
            trade_value = balance * POSITION_SIZE_PCT
            fee = trade_value * FEE_RATE
            ev = (0.40 * balance * REWARD_PER_TRADE_PCT) - (0.60 * balance * RISK_PER_TRADE_PCT) - fee
            balance += ev
        daily_gain_usd = (balance - prev) * NZD_TO_USD
        sign = "+" if daily_gain_usd >= 0 else ""
        print(f"  {day:<6}{balance:>14.2f}     {sign}${daily_gain_usd:.2f}")

    print()

    # Current live stats if trade_log has data
    if os.path.exists(TRADE_LOG):
        trades = []
        with open(TRADE_LOG) as f:
            for line in f:
                line = line.strip()
                if line:
                    t = json.loads(line)
                    if t.get("status") == "closed":
                        trades.append(t)
        if trades:
            n = len(trades)
            wins = len([t for t in trades if t["pnl_usd"] > 0])
            actual_wr = wins / n
            total_pnl = sum(t["pnl_usd"] for t in trades)
            print(f"  LIVE STATS ({n} trades): Win Rate {actual_wr:.0%} | Total PnL ${total_pnl:.4f}")

    print("=" * 60)


if __name__ == "__main__":
    project_table()
