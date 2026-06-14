#!/usr/bin/env python3
"""
Binance Spot Copy Trading Screener
Queries Binance's public leaderboard API and ranks traders by quality score.
Run: C:\python314\python.exe -u C:\BinanceBot\copy_scout.py
"""

import requests
import json
import time
from datetime import datetime

# ── Screening criteria ────────────────────────────────────────────────────────
MIN_DAYS          = 90       # minimum trading history (days)
MIN_COPIERS       = 30       # minimum follower count
MAX_DRAWDOWN      = 15.0     # max drawdown %
MIN_WIN_RATE      = 60.0     # minimum win rate %
MAX_WIN_RATE      = 85.0     # flag suspiciously high (martingale risk above this)
MIN_ROI_30D       = 3.0      # minimum 30-day ROI %
MAX_ROI_30D       = 50.0     # flag suspiciously high monthly ROI above this
RESULTS_TO_SHOW   = 10       # top N after scoring

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "clienttype": "web",
    "lang": "en",
}

ENDPOINTS = [
    # Spot-specific endpoint
    "https://www.binance.com/bapi/futures/v1/public/future/spot-copy-trade/lead-portfolio/query-ranking",
    # Generic copy-trade endpoint with type filter
    "https://www.binance.com/bapi/futures/v1/public/future/copy-trade/lead-portfolio/query-ranking",
    # Older list endpoints
    "https://www.binance.com/bapi/futures/v1/public/future/spot-copy-trade/lead-portfolio/list",
    "https://www.binance.com/bapi/futures/v1/public/future/copy-trade/lead-portfolio/list",
]

def try_fetch(url, payload):
    try:
        r = requests.post(url, headers=HEADERS, json=payload, timeout=15)
        if r.status_code == 200:
            data = r.json()
            # Various response shapes Binance uses
            d = data.get("data", {})
            if isinstance(d, list):
                return d
            if isinstance(d, dict):
                for key in ("list", "rankList", "portfolioList", "data"):
                    if key in d and isinstance(d[key], list):
                        return d[key]
        return None
    except Exception as e:
        return None

def fetch_all_traders():
    """Try every known endpoint + sort combination to build a broad pool."""
    all_traders = {}

    sort_types = ["COPIER_NUM", "ROI", "PNL", "WIN_RATIO"]
    payloads_per_sort = []
    for sort in sort_types:
        payloads_per_sort.append({
            "copyTradeType": "SPOT",
            "pageNumber": 1,
            "pageSize": 100,
            "sortType": sort,
        })
        payloads_per_sort.append({
            "pageNumber": 1,
            "pageSize": 100,
            "sortType": sort,
        })

    working_endpoint = None

    for url in ENDPOINTS:
        if working_endpoint:
            break
        for payload in payloads_per_sort[:2]:   # just test first two payloads
            result = try_fetch(url, payload)
            if result is not None and len(result) > 0:
                working_endpoint = url
                for t in result:
                    pid = t.get("leadPortfolioId") or t.get("portfolioId")
                    if pid:
                        all_traders[pid] = t
                print(f"  ✓ Connected via: {url}")
                break

    if not working_endpoint:
        return {}, None

    # Now pull remaining sort orders from working endpoint
    for payload in payloads_per_sort[2:]:
        result = try_fetch(working_endpoint, payload)
        if result:
            for t in result:
                pid = t.get("leadPortfolioId") or t.get("portfolioId")
                if pid:
                    all_traders[pid] = t
        time.sleep(0.3)

    return all_traders, working_endpoint

def normalize(t):
    """Normalize field names across different API response shapes."""
    def g(*keys):
        for k in keys:
            v = t.get(k)
            if v is not None:
                return v
        return None

    roi_raw      = g("roi", "roiRate", "returnRate", "profitRate")
    dd_raw       = g("maxDrawdownRatio", "maxDrawdown", "drawdownRatio", "maxRetracement")
    wr_raw       = g("winRatio", "winRate", "profitRatio", "winTradeRatio")
    copiers      = g("followerNum", "copierNum", "copyTraderNum", "followerCount")
    days         = g("runningDays", "tradingDays", "activeDays", "daysCnt")
    aum          = g("aum", "totalAUM", "copyAmount", "followAmount")
    name         = g("nickName", "nickname", "name", "traderNickname")
    pid          = g("leadPortfolioId", "portfolioId", "traderId")

    # Values from API: roi is often a decimal (0.15 = 15%) — detect and convert
    def to_pct(v, name=""):
        if v is None:
            return 0.0
        v = float(v)
        # If absolute value < 5 and it's a ratio field, treat as decimal
        if abs(v) < 5.0:
            return v * 100.0
        return v

    return {
        "pid":       pid or "",
        "name":      name or f"Trader-{str(pid)[:8]}",
        "roi_30d":   to_pct(roi_raw, "roi"),
        "drawdown":  to_pct(dd_raw, "dd"),
        "win_rate":  to_pct(wr_raw, "wr"),
        "copiers":   int(copiers or 0),
        "days":      int(days or 0),
        "aum":       float(aum or 0),
        "_raw":      t,
    }

def score_trader(n):
    """Score 0-100. Returns -1 if fails hard filter."""
    flags = []

    # ── Hard filters ──────────────────────────────────────────────────────────
    if n["days"] < MIN_DAYS:
        return -1, [f"Too new ({n['days']}d)"]
    if n["copiers"] < MIN_COPIERS:
        return -1, [f"Too few copiers ({n['copiers']})"]
    if n["drawdown"] > MAX_DRAWDOWN:
        return -1, [f"Drawdown {n['drawdown']:.1f}% > {MAX_DRAWDOWN}%"]
    if n["win_rate"] < MIN_WIN_RATE:
        return -1, [f"Win rate {n['win_rate']:.1f}% < {MIN_WIN_RATE}%"]
    if n["roi_30d"] < MIN_ROI_30D:
        return -1, [f"30d ROI {n['roi_30d']:.1f}% < {MIN_ROI_30D}%"]

    # ── Warning flags ─────────────────────────────────────────────────────────
    if n["win_rate"] > MAX_WIN_RATE:
        flags.append(f"⚠  Win rate {n['win_rate']:.1f}% > {MAX_WIN_RATE}% — possible martingale")
    if n["roi_30d"] > MAX_ROI_30D:
        flags.append(f"⚠  ROI {n['roi_30d']:.1f}% > {MAX_ROI_30D}% — likely high leverage or luck")

    # ── Scoring (0-100) ───────────────────────────────────────────────────────
    # Drawdown: 30 pts — lower is better
    dd_score  = max(0, 30 * (1 - n["drawdown"] / MAX_DRAWDOWN))

    # Win rate: 25 pts — sweet spot 65-72%
    wr_diff   = abs(n["win_rate"] - 68.0)
    wr_score  = max(0, 25 - wr_diff * 1.2)

    # ROI: 20 pts — capped at 30% (beyond that = risky)
    roi_score = min(20, (min(n["roi_30d"], 30.0) / 30.0) * 20)

    # Social proof: 15 pts — copiers (cap at 1000)
    cop_score = min(15, (n["copiers"] / 1000) * 15)

    # Experience: 10 pts — cap at 1 year
    exp_score = min(10, (n["days"] / 365) * 10)

    total = dd_score + wr_score + roi_score + cop_score + exp_score
    return round(total, 1), flags

def print_result(rank, n, score, flags):
    pid = n["pid"]
    url = f"https://www.binance.com/en/copy-trading/spot/portfolio/{pid}"

    print(f"\n  {'='*58}")
    print(f"  #{rank:<3}  {n['name']}   [Score: {score}/100]")
    print(f"  {'='*58}")
    print(f"  30d ROI     : {n['roi_30d']:+.2f}%")
    print(f"  Max Drawdown: {n['drawdown']:.2f}%")
    print(f"  Win Rate    : {n['win_rate']:.1f}%")
    print(f"  Copiers     : {n['copiers']:,}")
    print(f"  Running     : {n['days']} days")
    print(f"  AUM         : ${n['aum']:,.0f}")
    print(f"  Profile     : {url}")
    for f in flags:
        print(f"  {f}")

def main():
    print(f"\n{'#'*62}")
    print(f"  BINANCE SPOT COPY TRADING SCREENER  —  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'#'*62}")
    print(f"\n  Filters: ≥{MIN_DAYS}d  |  ≥{MIN_COPIERS} copiers  |  drawdown ≤{MAX_DRAWDOWN}%")
    print(f"           win rate {MIN_WIN_RATE}-{MAX_WIN_RATE}%  |  30d ROI ≥{MIN_ROI_30D}%")
    print(f"\n  Fetching leaderboard from Binance API...")

    raw_traders, endpoint = fetch_all_traders()

    if not raw_traders:
        print("\n  [BLOCKED] Binance API rejected all requests.")
        print("  Binance requires browser cookies for the copy-trading API.")
        print()
        print("  ── MANUAL FALLBACK (3 minutes) ──────────────────────────")
        print("  1. Open: https://www.binance.com/en/copy-trading/spot")
        print("  2. Click Filter (top right of trader list)")
        print("  3. Set:  Max Drawdown  → 15%")
        print("           Win Rate      → 60% – 80%")
        print("           Running Days  → 90")
        print("           ROI (30d)     → 3% – 40%")
        print("  4. Sort by: Copiers")
        print("  5. Open top 5 profiles, check equity curve + largest loss")
        print("  6. Report names back here — I'll score them for you.")
        return

    print(f"  Fetched {len(raw_traders)} unique traders. Scoring...\n")

    scored = []
    for pid, raw in raw_traders.items():
        n = normalize(raw)
        score, flags = score_trader(n)
        if score >= 0:
            scored.append((score, n, flags))

    scored.sort(key=lambda x: x[0], reverse=True)

    passes = len(scored)
    total  = len(raw_traders)
    print(f"  {passes} / {total} traders passed all filters.")

    if not scored:
        print("\n  No traders passed. Raw sample of first trader for debugging:")
        sample = next(iter(raw_traders.values()))
        for k, v in list(sample.items())[:15]:
            print(f"    {k}: {v}")
        print("\n  Adjust MIN_DAYS / MIN_COPIERS / MAX_DRAWDOWN at top of script.")
        return

    top = scored[:RESULTS_TO_SHOW]
    print(f"\n  TOP {len(top)} TRADERS TO CONSIDER:")

    for i, (score, n, flags) in enumerate(top, 1):
        print_result(i, n, score, flags)

    print(f"\n  {'='*58}")
    print("  RECOMMENDED NEXT STEPS:")
    print("  1. Open the top 2-3 profile URLs above in your browser")
    print("  2. Check equity curve — want smooth slope, not cliff edges")
    print("  3. Check 'Largest Single Loss' on their profile")
    print("  4. Click Copy → enter $100–150 allocation")
    print("  5. Set Copy Stop-Loss = 15%")
    print("  6. Use FIXED AMOUNT mode (not proportional)")
    print(f"  {'='*58}\n")

if __name__ == "__main__":
    main()
