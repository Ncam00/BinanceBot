#!/usr/bin/env python3
"""
Loss diagnostic tool — reads data/trades.db and identifies why the bot is losing.
Run: python analyze_trades.py [--days N] [--json]
"""
import sys
import argparse
import sqlite3
import math
import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

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

DB_PATH = Path(__file__).parent / "data" / "trades.db"

FEE_RATE       = 0.001
SLIPPAGE_RATE  = 0.0005
ROUND_TRIP_FEE = FEE_RATE * 2 + SLIPPAGE_RATE   # 0.0025
POSITION_USDT  = 60.0
DAILY_PROFIT_CAP  = 5.0
DAILY_LOSS_LIMIT  = 7.0

MIN_TRADES = 30  # minimum for reliable analysis

# NZST session windows (local hours)
EU_START, EU_END = 19, 23
US_START, US_END = 1, 5


# ─── helpers ────────────────────────────────────────────────────────────────

def load_trades(db_path: Path, days: int = None):
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM trades WHERE exit_time IS NOT NULL"
        params = []
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            query += " AND entry_time > ?"
            params.append(cutoff)
        query += " ORDER BY exit_time ASC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def wilson_ci(wins: int, n: int, z: float = 1.96):
    """Wilson score 95% confidence interval for a proportion."""
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    centre = (p + z**2 / (2 * n)) / (1 + z**2 / n)
    margin = (z / (1 + z**2 / n)) * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return max(0.0, centre - margin), min(1.0, centre + margin)


def section(title: str):
    width = 62
    print(f"\n{'═' * width}")
    print(f"  {title}")
    print(f"{'═' * width}")


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def dollar(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:.2f}"


def session_label(entry_time_str: str) -> str:
    if not entry_time_str:
        return "UNKNOWN"
    try:
        hour = datetime.fromisoformat(entry_time_str).hour
        if EU_START <= hour <= EU_END:
            return "EU"
        if hour <= US_END or hour == 0:
            return "US"
        return "OTHER"
    except Exception:
        return "UNKNOWN"


# ─── diagnostic sections ────────────────────────────────────────────────────

def print_sample_check(trades: list) -> bool:
    """Returns True if there are enough trades to analyse."""
    n = len(trades)
    section("1. SAMPLE SIZE CHECK")

    if n == 0:
        print("\n  ⛔  No completed trades found in the database.")
        print(f"       DB path checked: {DB_PATH}")
        print("\n  Run the bot in paper mode (DRY_RUN = True) until trades close,")
        print("  then re-run this script.")
        return False

    lo, hi = wilson_ci(sum(1 for t in trades if (t["pnl_usdt"] or 0) > 0), n)
    margin = (hi - lo) / 2

    rows = [
        ["Completed trades", n],
        ["Minimum recommended", MIN_TRADES],
        ["Win-rate margin (±95% CI)", f"±{margin * 100:.0f}%"],
    ]
    print()
    print(tabulate(rows, tablefmt="plain"))

    if n < MIN_TRADES:
        remaining = MIN_TRADES - n
        trades_per_day = max(1, n / max(1, _days_span(trades)))
        eta_days = math.ceil(remaining / trades_per_day)
        print(f"\n  ⚠  Only {n} trades — results are directional, not conclusive.")
        print(f"     Need ~{remaining} more trades (~{eta_days} more day(s) at current rate).")
    else:
        print(f"\n  ✅  {n} trades — sufficient for pattern analysis.")
    return True


def print_overall_summary(trades: list):
    section("2. OVERALL P&L SUMMARY")
    wins   = [t for t in trades if (t["pnl_usdt"] or 0) > 0]
    losses = [t for t in trades if (t["pnl_usdt"] or 0) <= 0]
    n = len(trades)
    win_rate = len(wins) / n if n else 0

    total_pnl   = sum(t["pnl_usdt"] or 0 for t in trades)
    gross_wins  = sum(t["pnl_usdt"] or 0 for t in wins)
    gross_losses= sum(t["pnl_usdt"] or 0 for t in losses)
    avg_win     = gross_wins  / len(wins)   if wins   else 0
    avg_loss    = gross_losses/ len(losses) if losses else 0

    lo, hi = wilson_ci(len(wins), n)

    rows = [
        ["Total trades",      n],
        ["Wins / Losses",     f"{len(wins)} / {len(losses)}"],
        ["Win rate",          f"{pct(win_rate)}  (95% CI: {pct(lo)} – {pct(hi)})"],
        ["Net P&L",           dollar(total_pnl)],
        ["Gross wins",        dollar(gross_wins)],
        ["Gross losses",      dollar(gross_losses)],
        ["Avg winner",        dollar(avg_win)],
        ["Avg loser",         dollar(avg_loss)],
        ["Profit factor",     f"{abs(gross_wins/gross_losses):.2f}" if gross_losses else "∞"],
    ]
    print()
    print(tabulate(rows, tablefmt="plain"))

    breakeven_wr = 1 / (1 + 2)  # 33.3% for pure 1:2
    print(f"\n  Breakeven win rate for 1:2 R:R: {pct(breakeven_wr)}")
    if win_rate < breakeven_wr:
        print(f"  ❌  Win rate {pct(win_rate)} is BELOW breakeven — losing even with good R:R.")
    else:
        print(f"  ✅  Win rate above breakeven.")


def print_fee_drag(trades: list):
    section("3. FEE DRAG ANALYSIS")
    n = len(trades)
    if not n:
        return

    est_fee_per_trade = POSITION_USDT * ROUND_TRIP_FEE
    total_fees = est_fee_per_trade * n
    total_pnl  = sum(t["pnl_usdt"] or 0 for t in trades)
    gross_losses = abs(sum(t["pnl_usdt"] or 0 for t in trades if (t["pnl_usdt"] or 0) < 0))
    fee_pct_of_losses = (total_fees / gross_losses * 100) if gross_losses else 0
    # hypothetical P&L if there were zero fees
    pnl_without_fees = total_pnl + total_fees

    rows = [
        ["Est. fee per trade (0.25% of $60)", f"${est_fee_per_trade:.2f}"],
        ["Total estimated fees paid",         dollar(-total_fees)],
        ["Actual net P&L",                    dollar(total_pnl)],
        ["P&L without fees (hypothetical)",   dollar(pnl_without_fees)],
        ["Fees as % of gross losses",         f"{fee_pct_of_losses:.1f}%"],
    ]
    print()
    print(tabulate(rows, tablefmt="plain"))

    if fee_pct_of_losses > 15:
        print(f"\n  ⚠  Fees account for {fee_pct_of_losses:.0f}% of gross losses — significant drag.")
    if pnl_without_fees > 0 and total_pnl < 0:
        print(f"\n  ⚠  WITHOUT fees the strategy would be PROFITABLE — fees are the main killer.")


def print_exit_reason_breakdown(trades: list):
    section("4. EXIT REASON BREAKDOWN")
    groups: dict = defaultdict(list)
    for t in trades:
        reason = (t.get("exit_reason") or "UNKNOWN").upper()
        groups[reason].append(t)

    rows = []
    for reason in ["SL", "TP1", "TP2", "TRAIL", "TIME", "UNKNOWN"]:
        bucket = groups.get(reason, [])
        if not bucket:
            continue
        wins     = sum(1 for t in bucket if (t["pnl_usdt"] or 0) > 0)
        avg_pnl  = sum(t["pnl_usdt"] or 0 for t in bucket) / len(bucket)
        total_pnl= sum(t["pnl_usdt"] or 0 for t in bucket)
        wr       = wins / len(bucket)
        rows.append([reason, len(bucket), f"{pct(wr)}", dollar(avg_pnl), dollar(total_pnl)])

    print()
    print(tabulate(rows,
                   headers=["Exit Reason", "Count", "Win Rate", "Avg P&L", "Total P&L"],
                   tablefmt="simple" if HAS_TABULATE else "plain"))

    # Flag notable patterns
    sl_bucket = groups.get("SL", [])
    time_bucket = groups.get("TIME", [])
    n = len(trades)
    if sl_bucket and len(sl_bucket) / n > 0.5:
        print(f"\n  ⚠  {len(sl_bucket)/n:.0%} of trades hit stop-loss — entries may be premature or SL too tight.")
    if time_bucket and len(time_bucket) / n > 0.2:
        print(f"\n  ⚠  {len(time_bucket)/n:.0%} time-exits — positions stalling, not progressing to TP.")


def print_rr_analysis(trades: list):
    section("5. ACTUAL R:R ACHIEVED")
    wins   = [t["pnl_usdt"] for t in trades if (t["pnl_usdt"] or 0) > 0]
    losses = [abs(t["pnl_usdt"]) for t in trades if (t["pnl_usdt"] or 0) < 0]
    if not wins or not losses:
        print("\n  Not enough data (need at least one win and one loss).")
        return

    avg_win  = sum(wins)  / len(wins)
    avg_loss = sum(losses)/ len(losses)
    actual_rr = avg_win / avg_loss

    rows = [
        ["Avg winning trade",  f"${avg_win:.2f}"],
        ["Avg losing trade",   f"${avg_loss:.2f}"],
        ["Actual R:R",         f"1 : {actual_rr:.2f}"],
        ["Target R:R",         "1 : 2.00"],
        ["Difference",         f"{(actual_rr - 2.0):+.2f}"],
    ]
    print()
    print(tabulate(rows, tablefmt="plain"))

    if actual_rr < 1.5:
        print(f"\n  ❌  Actual R:R of 1:{actual_rr:.1f} is well below target 1:2.")
        print("     Possible causes: trailing stop too tight (1.5%), time-exits,")
        print("     or SL being hit before TP1 on volatile candles.")
    elif actual_rr < 1.8:
        print(f"\n  ⚠  R:R slightly below target — winners are being cut short.")
    else:
        print(f"\n  ✅  R:R close to target.")


def print_session_breakdown(trades: list):
    section("6. SESSION BREAKDOWN  (EU 19-23h  |  US 01-05h)")
    groups: dict = defaultdict(list)
    for t in trades:
        groups[session_label(t.get("entry_time", ""))].append(t)

    rows = []
    for sess in ["EU", "US", "OTHER", "UNKNOWN"]:
        bucket = groups.get(sess, [])
        if not bucket:
            continue
        wins    = sum(1 for t in bucket if (t["pnl_usdt"] or 0) > 0)
        avg_pnl = sum(t["pnl_usdt"] or 0 for t in bucket) / len(bucket)
        wr      = wins / len(bucket)
        rows.append([sess, len(bucket), pct(wr), dollar(avg_pnl)])

    print()
    if rows:
        print(tabulate(rows, headers=["Session", "Trades", "Win Rate", "Avg P&L"],
                       tablefmt="simple" if HAS_TABULATE else "plain"))
    else:
        print("  No session data available.")


def print_win_rate_trend(trades: list):
    section("7. WIN RATE TREND  (last 10 vs previous 10)")
    if len(trades) < 10:
        print(f"\n  Need at least 10 trades — only {len(trades)} so far.")
        return

    def wr(bucket):
        wins = sum(1 for t in bucket if (t["pnl_usdt"] or 0) > 0)
        return wins / len(bucket) if bucket else 0

    chunks = []
    size = 10
    for i in range(0, len(trades), size):
        chunk = trades[i:i + size]
        chunks.append(chunk)

    rows = []
    for i, chunk in enumerate(chunks):
        label = f"Trades {i*size+1}–{i*size+len(chunk)}"
        rows.append([label, len(chunk), pct(wr(chunk)),
                     dollar(sum(t["pnl_usdt"] or 0 for t in chunk))])

    print()
    print(tabulate(rows, headers=["Period", "Count", "Win Rate", "P&L"],
                   tablefmt="simple" if HAS_TABULATE else "plain"))

    if len(chunks) >= 2:
        first_wr = wr(chunks[0])
        last_wr  = wr(chunks[-1])
        delta = last_wr - first_wr
        if delta > 0.05:
            print(f"\n  📈  Win rate IMPROVING (+{delta*100:.0f}pp).")
        elif delta < -0.05:
            print(f"\n  📉  Win rate DETERIORATING ({delta*100:.0f}pp) — strategy may be degrading.")
        else:
            print(f"\n  → Win rate roughly stable.")


def print_ranked_suspects(trades: list):
    section("8. RANKED LOSS SUSPECTS")
    suspects = []

    wins   = [t for t in trades if (t["pnl_usdt"] or 0) > 0]
    losses = [t for t in trades if (t["pnl_usdt"] or 0) <= 0]
    n = len(trades)
    win_rate = len(wins) / n if n else 0
    avg_win  = sum(t["pnl_usdt"] or 0 for t in wins)  / len(wins)  if wins  else 0
    avg_loss = abs(sum(t["pnl_usdt"] or 0 for t in losses)) / len(losses) if losses else 0
    actual_rr = avg_win / avg_loss if avg_loss else 0

    by_reason = defaultdict(list)
    for t in trades:
        by_reason[(t.get("exit_reason") or "UNKNOWN").upper()].append(t)

    sl_rate   = len(by_reason.get("SL",   [])) / n if n else 0
    time_rate = len(by_reason.get("TIME", [])) / n if n else 0
    est_total_fees = POSITION_USDT * ROUND_TRIP_FEE * n
    total_pnl = sum(t["pnl_usdt"] or 0 for t in trades)

    # Asymmetric daily limits
    suspects.append((
        "Asymmetric daily limits",
        "HIGH",
        f"Bot stops winning days at ${DAILY_PROFIT_CAP} but allows ${DAILY_LOSS_LIMIT} losses. "
        f"Losing days cost {DAILY_LOSS_LIMIT/DAILY_PROFIT_CAP:.0%} more than winning days earn."
    ))

    # Fee drag
    if est_total_fees > 0:
        fee_pct = abs(est_total_fees / total_pnl) if total_pnl < 0 else 0
        severity = "HIGH" if fee_pct > 0.3 else "MEDIUM" if fee_pct > 0.1 else "LOW"
        suspects.append((
            "Fee erosion (0.25% round-trip)",
            severity,
            f"~${est_total_fees:.2f} total fees on {n} trades. "
            f"Each $60 trade costs ~${POSITION_USDT * ROUND_TRIP_FEE:.2f} in fees before P&L."
        ))

    # Win rate below breakeven
    if win_rate < 0.33:
        suspects.append((
            "Win rate below breakeven",
            "CRITICAL",
            f"Win rate {pct(win_rate)} is below 33.3% breakeven for 1:2 R:R — losing regardless of R:R."
        ))
    elif win_rate < 0.40:
        suspects.append((
            "Win rate borderline",
            "HIGH",
            f"Win rate {pct(win_rate)} is only marginally above breakeven. Small degradation = losses."
        ))

    # R:R shortfall
    if 0 < actual_rr < 1.5:
        suspects.append((
            "R:R below target (winners cut short)",
            "HIGH",
            f"Achieving 1:{actual_rr:.1f} vs target 1:2. "
            "Trailing stop (1.5%) or time-exits may be cutting winners before TP1."
        ))

    # High SL rate
    if sl_rate > 0.5:
        suspects.append((
            "Excessive stop-loss hits",
            "HIGH",
            f"{sl_rate:.0%} of trades stopped out. "
            "Entry timing may be off, or SL (ATR×1.5) too tight for current volatility."
        ))

    # High time-exit rate
    if time_rate > 0.2:
        suspects.append((
            "Frequent time-exits (25 candles)",
            "MEDIUM",
            f"{time_rate:.0%} of trades exited after 25 candles with no progress. "
            "These often close at a loss or breakeven."
        ))

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    suspects.sort(key=lambda x: severity_order.get(x[1], 9))

    print()
    for rank, (name, severity, detail) in enumerate(suspects, 1):
        icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(severity, "⚪")
        print(f"  {rank}. {icon} [{severity}] {name}")
        print(f"       {detail}")
        print()


def print_projection(trades: list):
    section("9. DATA COLLECTION PROJECTION")
    if not trades:
        print("\n  No trades yet — cannot project rate.")
        return

    span_days = max(1, _days_span(trades))
    rate = len(trades) / span_days

    rows = []
    for target in [30, 50, 100]:
        remaining = max(0, target - len(trades))
        eta = math.ceil(remaining / rate) if remaining > 0 else 0
        status = "✅ Already reached" if remaining == 0 else f"~{eta} more day(s)"
        rows.append([target, len(trades), remaining, f"{rate:.1f}/day", status])

    print()
    print(tabulate(rows,
                   headers=["Target", "Have", "Need", "Rate", "ETA"],
                   tablefmt="simple" if HAS_TABULATE else "plain"))

    print()
    print("  Confidence levels:")
    print("    30 trades → directional patterns visible")
    print("    50 trades → statistical confidence (±14% win rate margin)")
    print("   100 trades → full strategy evaluation (±10% margin)")


# ─── helpers ────────────────────────────────────────────────────────────────

def _days_span(trades: list) -> float:
    if len(trades) < 2:
        return 1.0
    try:
        t0 = datetime.fromisoformat(trades[0]["entry_time"])
        t1 = datetime.fromisoformat(trades[-1]["entry_time"])
        return max(1.0, (t1 - t0).total_seconds() / 86400)
    except Exception:
        return 1.0


# ─── main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BinanceBot trade loss diagnostics")
    parser.add_argument("--days", type=int, default=None,
                        help="Analyse only trades from last N days")
    parser.add_argument("--json", action="store_true",
                        help="Also dump raw stats to analyze_report.json")
    args = parser.parse_args()

    trades = load_trades(DB_PATH, days=args.days)

    scope = f"last {args.days} days" if args.days else "all time"
    print(f"\n{'═' * 62}")
    print(f"  BINANCEBOT — LOSS DIAGNOSTIC REPORT  ({scope})")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 62}")

    has_data = print_sample_check(trades)
    if not has_data:
        sys.exit(0)

    print_overall_summary(trades)
    print_fee_drag(trades)
    print_exit_reason_breakdown(trades)
    print_rr_analysis(trades)
    print_session_breakdown(trades)
    print_win_rate_trend(trades)
    print_ranked_suspects(trades)
    print_projection(trades)

    print(f"\n{'═' * 62}")
    print("  Run again after collecting more paper trades for better accuracy.")
    print(f"{'═' * 62}\n")

    if args.json:
        n = len(trades)
        wins = [t for t in trades if (t["pnl_usdt"] or 0) > 0]
        report = {
            "generated": datetime.now().isoformat(),
            "total_trades": n,
            "win_rate": len(wins) / n if n else 0,
            "total_pnl": sum(t["pnl_usdt"] or 0 for t in trades),
            "trades": trades,
        }
        out = Path("analyze_report.json")
        out.write_text(json.dumps(report, indent=2, default=str))
        print(f"  JSON report written to {out}\n")


if __name__ == "__main__":
    main()
