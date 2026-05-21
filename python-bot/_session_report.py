import json
import datetime as dt
from collections import defaultdict

now = dt.datetime.now(dt.timezone.utc)
since = now - dt.timedelta(hours=24)

trades = []
for line in open("trade_log.jsonl", encoding="utf-8"):
    try:
        trades.append(json.loads(line))
    except Exception:
        pass


def ts(t):
    s = t.get("exit_time") or t.get("timestamp") or ""
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d
    except Exception:
        return None


def is_closed(t):
    return ("profit" in t) or (t.get("status") == "closed") or ("pnl_usd" in t)


def pnl(t):
    return t.get("profit", t.get("pnl_usd", 0))


closed = [t for t in trades if is_closed(t)]
recent = [t for t in closed if (ts(t) and ts(t) >= since)]

print(f"=== LAST 24H ({since:%Y-%m-%d %H:%M} -> {now:%Y-%m-%d %H:%M} UTC) ===")
print(f"Closed trades: {len(recent)}")

if recent:
    pnls = [pnl(t) for t in recent]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    print(f"Net PnL: ${sum(pnls):+.4f}")
    print(
        f"Wins: {len(wins)}  Losses: {len(losses)}  WinRate: {len(wins)/len(pnls)*100:.1f}%"
    )
    print(f"Best: ${max(pnls):+.4f}  Worst: ${min(pnls):+.4f}")

    by_sym = defaultdict(list)
    for t in recent:
        by_sym[t.get("pair") or t.get("symbol", "?")].append(pnl(t))
    print("--- by symbol ---")
    for s, v in by_sym.items():
        w = sum(1 for x in v if x > 0)
        print(f"  {s}: {len(v)} trades  WR {w}/{len(v)}  ${sum(v):+.4f}")

    by_reason = defaultdict(list)
    for t in recent:
        by_reason[t.get("exit_reason") or t.get("reason", "?")].append(pnl(t))
    print("--- by exit reason ---")
    for r, v in by_reason.items():
        print(f"  {r}: {len(v)}  ${sum(v):+.4f}")

    print("--- trades ---")
    for t in recent:
        sym = t.get("pair") or t.get("symbol", "?")
        r = t.get("exit_reason") or t.get("reason", "?")
        tt = t.get("trade_type", "-")
        sess = t.get("entry_session", "-")
        print(
            f"  {ts(t):%m-%d %H:%M}  {sym:10s} {str(tt):14s} {str(sess):7s} {r:14s} ${pnl(t):+.4f}"
        )

# session-to-date (since last bot restart approx: use last 12h as proxy for "this session")
sess_since = now - dt.timedelta(hours=12)
sess = [t for t in closed if (ts(t) and ts(t) >= sess_since)]
if sess:
    pnls = [pnl(t) for t in sess]
    print(
        f"\n=== LAST 12H (current trading session) ==="
    )
    print(f"  Trades: {len(sess)}  Net: ${sum(pnls):+.4f}")

# open positions
open_trades = [t for t in trades if t.get("status") == "open" and not is_closed(t)]
print(f"\nOpen 'open' rows in log: {len(open_trades)}")
