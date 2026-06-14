"""
analyze_trades.py — Loss diagnostic tool
Reads trade_log.jsonl and produces 9 diagnostic sections.
Run: python analyze_trades.py
"""

import json
import os
import math
from datetime import datetime, timezone
from collections import defaultdict

TRADE_LOG = os.path.join(os.path.dirname(__file__), "trade_log.jsonl")
FEE_RATE = 0.0025  # 0.25% round-trip (Binance taker × 2)
MIN_SAMPLE = 30


def load_trades(source=None):
    if not os.path.exists(TRADE_LOG):
        return []
    trades = []
    with open(TRADE_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                t = json.loads(line)
                if t.get("status") == "closed":
                    if source is None or t.get("source", "live") == source:
                        trades.append(t)
    return trades


def session(ts_str):
    dt = datetime.fromisoformat(ts_str).astimezone(timezone.utc)
    hour = dt.hour
    if 7 <= hour < 16:
        return "EU"
    elif 13 <= hour < 22:
        return "US"
    return "ASIA"


def wilson_ci(wins, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    spread = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return round(centre - spread, 3), round(centre + spread, 3)


def main():
    all_trades = load_trades()
    paper_trades = load_trades(source="paper")
    live_trades = load_trades(source="live")
    trades = all_trades
    n = len(trades)

    print("=" * 60)
    print("  SMART TRADER V3 — TRADE DIAGNOSTICS")
    print(f"  Paper: {len(paper_trades)} trades  |  Live: {len(live_trades)} trades  |  Total: {n}")
    print("=" * 60)

    # ----------------------------------------------------------
    # 1. SAMPLE SIZE CHECK
    # ----------------------------------------------------------
    print(f"\n[1] SAMPLE SIZE")
    print(f"    Completed trades : {n}")
    if n < MIN_SAMPLE:
        needed = MIN_SAMPLE - n
        days_needed = math.ceil(needed / max(1, n / max(1, 1)))
        print(f"    ⚠  Need {needed} more trades for statistical confidence.")
        print(f"       At current rate, ~{days_needed} more days of running required.")
    else:
        print(f"    ✓  Sufficient sample size (≥{MIN_SAMPLE})")

    if n == 0:
        print("\n    No closed trades yet. Run the bot to collect data.")
        return

    # ----------------------------------------------------------
    # 2. OVERALL P&L
    # ----------------------------------------------------------
    pnls = [t["pnl_usd"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / n
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    ci_lo, ci_hi = wilson_ci(len(wins), n)

    print(f"\n[2] OVERALL P&L")
    print(f"    Total PnL        : ${sum(pnls):.4f}")
    print(f"    Win Rate         : {win_rate:.1%}  (95% CI: {ci_lo:.1%} – {ci_hi:.1%})")
    print(f"    Profit Factor    : {profit_factor:.2f}  (>1.5 = good)")
    print(f"    Avg Win          : ${sum(wins)/len(wins):.4f}" if wins else "    Avg Win          : n/a")
    print(f"    Avg Loss         : ${sum(losses)/len(losses):.4f}" if losses else "    Avg Loss         : n/a")

    # ----------------------------------------------------------
    # 3. FEE DRAG
    # ----------------------------------------------------------
    total_cost = sum(t["entry"] * t["qty"] for t in trades)
    total_fees = total_cost * FEE_RATE
    print(f"\n[3] FEE DRAG")
    print(f"    Est. total fees  : ${total_fees:.4f}")
    print(f"    As % of losses   : {total_fees/gross_loss*100:.1f}%" if gross_loss > 0 else "    Losses           : $0")
    print(f"    Fee per trade    : ${total_fees/n:.4f}")

    # ----------------------------------------------------------
    # 4. EXIT REASON BREAKDOWN
    # ----------------------------------------------------------
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t.get("reason", "UNKNOWN")].append(t["pnl_usd"])

    print(f"\n[4] EXIT REASON BREAKDOWN")
    for reason, ps in sorted(by_reason.items()):
        wr = len([p for p in ps if p > 0]) / len(ps)
        print(f"    {reason:<16} count={len(ps):>3}  win={wr:.0%}  avg=${sum(ps)/len(ps):.4f}")

    # ----------------------------------------------------------
    # 5. ACTUAL R:R ACHIEVED
    # ----------------------------------------------------------
    if wins and losses:
        avg_win = sum(wins) / len(wins)
        avg_loss = abs(sum(losses) / len(losses))
        actual_rr = avg_win / avg_loss
        print(f"\n[5] ACTUAL R:R ACHIEVED")
        print(f"    Avg Win / Avg Loss : {actual_rr:.2f}:1  (target ≥2.0:1)")
        if actual_rr < 1.5:
            print(f"    ⚠  R:R below 1.5 — consider widening TP or tightening SL")
        else:
            print(f"    ✓  R:R is healthy")

    # ----------------------------------------------------------
    # 6. SESSION BREAKDOWN
    # ----------------------------------------------------------
    by_session = defaultdict(list)
    for t in trades:
        by_session[session(t["timestamp"])].append(t["pnl_usd"])

    print(f"\n[6] SESSION BREAKDOWN")
    for sess, ps in sorted(by_session.items()):
        wr = len([p for p in ps if p > 0]) / len(ps)
        print(f"    {sess:<6} count={len(ps):>3}  win={wr:.0%}  total=${sum(ps):.4f}")

    # ----------------------------------------------------------
    # 7. WIN RATE TREND (rolling 10)
    # ----------------------------------------------------------
    print(f"\n[7] WIN RATE TREND (rolling 10 trades)")
    if n >= 10:
        chunks = [pnls[i:i+10] for i in range(0, n - 9, 10)]
        for i, chunk in enumerate(chunks):
            wr = len([p for p in chunk if p > 0]) / len(chunk)
            bar = "█" * int(wr * 20)
            print(f"    Block {i+1:>2} (trades {i*10+1}-{i*10+len(chunk)}) : {bar:<20} {wr:.0%}")
    else:
        print(f"    Need ≥10 trades for trend analysis")

    # ----------------------------------------------------------
    # 8. RANKED LOSS SUSPECTS
    # ----------------------------------------------------------
    print(f"\n[8] RANKED LOSS SUSPECTS")
    suspects = []
    if win_rate < 0.35:
        suspects.append(("CRITICAL", "Win rate below 35% — entry signals not selective enough"))
    if profit_factor < 1.0:
        suspects.append(("CRITICAL", "Profit factor <1.0 — losing more than winning in dollar terms"))
    if wins and losses and (sum(wins)/len(wins)) < (abs(sum(losses)/len(losses))):
        suspects.append(("HIGH", "Average win smaller than average loss — TP too tight or SL too wide"))
    sl_exits = len(by_reason.get("STOP LOSS", []))
    if sl_exits / n > 0.5:
        suspects.append(("HIGH", f"Stop Loss exits = {sl_exits/n:.0%} of trades — ATR-based SL may be too tight"))
    time_exits = len(by_reason.get("TIME EXIT", []))
    if time_exits / n > 0.4:
        suspects.append(("MEDIUM", f"TIME EXIT = {time_exits/n:.0%} — trades not reaching TP in time window"))
    fee_impact = total_fees / abs(sum(pnls)) if sum(pnls) < 0 else 0
    if fee_impact > 0.2:
        suspects.append(("MEDIUM", f"Fees account for {fee_impact:.0%} of losses"))

    if not suspects:
        print("    ✓  No major loss suspects found")
    for severity, msg in suspects:
        print(f"    [{severity}] {msg}")

    # ----------------------------------------------------------
    # 9. PROJECTION
    # ----------------------------------------------------------
    print(f"\n[9] PROJECTION")
    trades_per_day = n / max(1, (
        (datetime.fromisoformat(trades[-1]["timestamp"]) -
         datetime.fromisoformat(trades[0]["timestamp"])).total_seconds() / 86400
    )) if n > 1 else 1

    for target in [30, 50, 100]:
        days = math.ceil((target - n) / max(0.1, trades_per_day))
        print(f"    {target:>3} trades : ~{days} more days  (at {trades_per_day:.1f} trades/day)")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
