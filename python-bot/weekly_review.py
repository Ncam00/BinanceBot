"""Weekly trade review — run every Wednesday for the BinanceBot.

Usage:
    python weekly_review.py                       # current week (Mon 00:00 UTC -> now)
    python weekly_review.py --since 2026-05-20    # since a specific date
    python weekly_review.py --week-start 2026-05-18

Outputs:
    - PnL vs $7/week target, win rate, expectancy
    - Breakdowns by pair, session, trade type, exit reason
    - Diagnostic flags for common failure patterns
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

WEEKLY_TARGET_USD = 7.00
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_log.jsonl")


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def load_trades(path):
    if not os.path.exists(path):
        print(f"ERROR: log not found at {path}")
        sys.exit(1)
    trades = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"WARN: line {i} bad JSON: {e}")
    return trades


def in_window(t, start, end):
    ts = parse_iso(t.get("exit_time") or t.get("entry_time"))
    if not ts:
        return False
    if ts.tzinfo:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return start <= ts <= end


def fmt_usd(x):
    return f"${x:+.2f}" if x is not None else "$?.??"


def pct(num, denom):
    return (100.0 * num / denom) if denom else 0.0


def group_stats(trades, key_fn):
    buckets = defaultdict(list)
    for t in trades:
        k = key_fn(t)
        buckets[k or "?"].append(t)
    rows = []
    for k, ts in buckets.items():
        p = sum(t.get("profit", 0) for t in ts)
        w = sum(1 for t in ts if t.get("win"))
        rows.append((k, len(ts), w, p))
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows


def print_table(title, rows):
    print(f"\n  {title}")
    print(f"    {'Bucket':<20} {'Trades':>7} {'Wins':>5} {'PnL':>10} {'WR%':>6}")
    print(f"    {'-' * 52}")
    for k, n, w, p in rows:
        print(f"    {str(k):<20} {n:>7} {w:>5} {fmt_usd(p):>10} {pct(w, n):>5.1f}%")


def diagnose(trades):
    flags = []
    losers = [t for t in trades if not t.get("win") and t.get("profit", 0) < 0]
    if not losers:
        return ["No losses this week — clean book."]

    time_exits = [t for t in losers if "TIME" in (t.get("exit_reason") or "").upper()]
    if len(time_exits) >= 2:
        flags.append(
            f"WARN: TIME EXIT caused {len(time_exits)}/{len(losers)} losses — "
            f"consider extending TIME_EXIT_CANDLES or guarding by price."
        )

    counter = [t for t in losers if t.get("htf_bullish_at_entry") is False]
    if counter:
        flags.append(
            f"WARN: {len(counter)} losers entered against 1h trend — "
            f"check htf_trend_bullish wiring."
        )

    scouts = [t for t in trades if (t.get("trade_type") or "").upper().startswith("SCOUT")]
    scout_pnl = sum(t.get("profit", 0) for t in scouts)
    if scouts and scout_pnl < 0:
        flags.append(
            f"WARN: SCOUT trades net {fmt_usd(scout_pnl)} over {len(scouts)} — "
            f"consider disabling SCOUT or shrinking position_boost."
        )

    sess = defaultdict(float)
    for t in trades:
        sess[t.get("entry_session") or "?"] += t.get("profit", 0)
    if sess:
        worst = min(sess.items(), key=lambda x: x[1])
        if worst[1] < -1.0:
            flags.append(
                f"WARN: {worst[0].upper()} session net {fmt_usd(worst[1])} — "
                f"tighten filters or skip that window."
            )

    gross = sum(abs(t.get("profit", 0)) for t in trades)
    net = sum(t.get("profit", 0) for t in trades)
    if gross > 0 and abs(net) / gross < 0.10 and len(trades) >= 5:
        flags.append(
            f"WARN: Net is only {pct(abs(net), gross):.1f}% of gross — fees eating edge. "
            f"Need bigger moves per trade or fewer entries."
        )

    return flags or ["No clear failure patterns detected."]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="ISO date e.g. 2026-05-20")
    ap.add_argument("--week-start", default=None, help="ISO date of Monday e.g. 2026-05-18")
    args = ap.parse_args()

    now = datetime.utcnow()
    if args.since:
        start = datetime.fromisoformat(args.since)
        end = now
        label = f"since {start.date()}"
    elif args.week_start:
        start = datetime.fromisoformat(args.week_start)
        end = start + timedelta(days=7)
        label = f"week of {start.date()}"
    else:
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
        label = f"current week (from {start.date()})"

    all_trades = load_trades(LOG_PATH)
    trades = [t for t in all_trades if in_window(t, start, end)]

    print("=" * 60)
    print(f"  WEEKLY REVIEW — {label}")
    print(f"  Window: {start} -> {end}")
    print("=" * 60)

    if not trades:
        print(f"\n  No trades in window. Total in log: {len(all_trades)}")
        return

    total_pnl = sum(t.get("profit", 0) for t in trades)
    wins = sum(1 for t in trades if t.get("win"))
    losses = sum(1 for t in trades if not t.get("win"))
    win_amts = [t["profit"] for t in trades if t.get("win") and t.get("profit", 0) > 0]
    loss_amts = [t["profit"] for t in trades if not t.get("win") and t.get("profit", 0) < 0]
    avg_win = sum(win_amts) / len(win_amts) if win_amts else 0
    avg_loss = sum(loss_amts) / len(loss_amts) if loss_amts else 0
    expectancy = (wins * avg_win + losses * avg_loss) / len(trades) if trades else 0

    print(f"\n  HEADLINE")
    print(f"    Trades:       {len(trades)}")
    print(f"    Net PnL:      {fmt_usd(total_pnl)}")
    print(f"    Win rate:     {pct(wins, len(trades)):.1f}% ({wins}W / {losses}L)")
    print(f"    Avg win:      {fmt_usd(avg_win)}")
    print(f"    Avg loss:     {fmt_usd(avg_loss)}")
    print(f"    Expectancy:   {fmt_usd(expectancy)}/trade")

    pct_target = pct(total_pnl, WEEKLY_TARGET_USD)
    bar_len = int(min(max(pct_target, 0), 100) / 5)
    bar = "#" * bar_len + "." * (20 - bar_len)
    status = "HIT" if total_pnl >= WEEKLY_TARGET_USD else ("MISS" if total_pnl < 0 else "PARTIAL")
    print(f"\n  GOAL STATUS")
    print(f"    Target:       ${WEEKLY_TARGET_USD:.2f}/week")
    print(f"    Progress:     [{bar}] {pct_target:.0f}%  [{status}]")

    print_table("BY PAIR", group_stats(trades, lambda t: t.get("pair")))
    print_table("BY SESSION (at entry)", group_stats(trades, lambda t: t.get("entry_session")))
    print_table("BY TRADE TYPE", group_stats(trades, lambda t: t.get("trade_type")))
    print_table("BY EXIT REASON", group_stats(trades, lambda t: t.get("exit_reason")))

    print(f"\n  DIAGNOSTICS")
    for flag in diagnose(trades):
        print(f"    {flag}")

    holds = [t.get("hold_minutes") for t in trades if t.get("hold_minutes") is not None]
    print(f"\n  HOLD TIMES")
    if holds:
        print(f"    avg: {sum(holds)/len(holds):.1f} min | min: {min(holds):.1f} | max: {max(holds):.1f}")
    else:
        print("    (no hold_minutes data yet — appears in trades logged after enhanced logging)")

    print("\n" + "=" * 60)
    print("  NEXT WEEK ACTION ITEMS")
    print("=" * 60)
    if total_pnl >= WEEKLY_TARGET_USD:
        print(f"  PASS: target hit. Consider next phase (${WEEKLY_TARGET_USD * 2:.0f}/week) after 1 more hit week.")
    else:
        short = WEEKLY_TARGET_USD - total_pnl
        print(f"  MISS: ${short:.2f} short. Pick ONE fix from diagnostics above.")
    print(f"  -> Only change 1-2 things per week so impact is measurable.")
    print()


if __name__ == "__main__":
    main()
