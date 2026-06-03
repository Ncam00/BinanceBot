#!/usr/bin/env python3
"""
Binance Spot Copy Trading - 24/7 Monitor (Playwright edition)
Polls every 5 minutes via an authenticated Chromium browser session.

First run:
  1. Run this script.
  2. A Chromium window opens — log in to Binance.
  3. After login, the monitor takes over automatically.

Subsequent runs: login state is cached in C:\\BinanceBot\\browser_data\\.

Run: C:\\python314\\python.exe -u C:\\BinanceBot\\copy_monitor.py
"""

import os
import time
import winsound
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WATCHLIST     = {}   # manual watchlist (auto-discovery now handles entry)

# Traders you are CURRENTLY copying. Updated automatically on auto-start/stop.
ACTIVE_COPIES = {"\u9ed1\u76ae\u54e5\u54e5": 100.0}   # name -> USDT allocated

# ---- Position sizing -------------------------------------------------------
CAPITAL_PER_TRADER = 100.0   # base USDT per trader
MAX_COPIES         = 5        # never hold more than this many at once
MAX_DEPLOYED       = 500.0    # hard cap on total USDT deployed across all copies
DAILY_TARGET       = 5.0      # $5/day goal — logged each poll for tracking

# ---- Entry thresholds (auto-start when ALL met) ----------------------------
MIN_DAYS      = 30     # ≥30 days history (relaxed to find more candidates)
MAX_MDD       = 15.0   # max drawdown %
MIN_ROI_30D   = 8.0    # minimum 30-day ROI %
MAX_ROI_30D   = 150.0  # cap — filters obvious martingale blow-ups
MIN_WIN_RATE  = 55.0   # minimum win rate % (if available)
MIN_SHARPE    = 0.5    # minimum Sharpe ratio
MIN_SLOTS     = 1      # must have at least 1 free slot
MIN_SCORE     = 55     # minimum composite score (0-100)

# ---- Exit thresholds (auto-stop when ANY hit) ------------------------------
EXIT_MDD           = 20.0   # hard exit: MDD crosses this
EXIT_ROI_FLOOR     = -5.0   # hard exit: 30D ROI goes negative beyond this
EXIT_MDD_SPIKE     = 7.0    # exit if MDD rises by this much since we started copying
EXIT_ROI_DROP      = 15.0   # exit if ROI drops by this much since peak

# ---------------------------------------------------------------------------
# Trader IDs for fully automated stop/start (no browser needed)
# lead_id  = from the trader's profile URL
# copy_id  = YOUR copy portfolio ID (captured by capture_session.py)
# ---------------------------------------------------------------------------
TRADER_IDS = {
    "黑皮哥哥": {
        "lead_id": "4878524971286521088",
        "copy_id": "5075159579394172161",  # your copy portfolio ID
    },
    # Add new traders here after running capture_session.py:
    # "华子弟": {
    #     "lead_id": "5010515263519011585",
    #     "copy_id": "<captured after copying>",
    # },
}

MIN_DAYS      = 30     # kept for compatibility (now in config block above)
MAX_MDD       = 15.0
MIN_ROI_30D   = 8.0
MAX_ROI_30D   = 150.0

# Exit thresholds for traders you are actively copying
EXIT_MDD      = 20.0
EXIT_ROI_FLOOR = -5.0

POLL_INTERVAL = 300    # seconds between polls (5 min)
LOG_FILE      = r"C:\BinanceBot\copy_alerts.log"
DATA_DIR      = r"C:\BinanceBot\browser_data"   # persistent browser state

ENDPOINT = (
    "https://www.binance.com/bapi/futures/v1/friendly/"
    "future/spot-copy-trade/common/home-page-list"
)
BASE_PAYLOAD = {
    "pageNumber":      1,
    "pageSize":        50,
    "timeRange":       "30D",
    "dataType":        "ROI",
    "favoriteOnly":    False,
    "hideFull":        False,
    "nickname":        "",
    "order":           "DESC",
    "portfolioType":   "ALL",
    "userAsset":       0,
    "useAiRecommended": False,
    "PAGE_SIZE":        50,
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

previous_alerts: set = set()
_pw      = None
_browser = None
_page    = None

# Tracks stats at the time we started copying each trader (for trend exits)
# { trader_name: {"mdd_at_entry": float, "roi_at_entry": float, "roi_peak": float} }
_entry_snapshots: dict = {}

# JS helper for authenticated BAPI calls (reuses copy_monitor's own page)
_TRADE_JS = """
async (args) => {
    const r = await fetch(args.url, {
        method: 'POST',
        credentials: 'include',
        headers: {'content-type': 'application/json', 'clienttype': 'web'},
        body: JSON.stringify(args.body),
    });
    try { return await r.json(); }
    catch(e) { return {code: '?', _text: await r.text()}; }
}
"""


# ---------------------------------------------------------------------------
# Automated trading helpers (use the monitor's own authenticated page)
# ---------------------------------------------------------------------------

def _trade_post(path: str, body: dict) -> dict:
    """POST to Binance BAPI using the monitor's live browser session."""
    page = _get_page()
    return page.evaluate(_TRADE_JS, {"url": f"https://www.binance.com{path}", "body": body})


def execute_stop(trader_name: str) -> None:
    """Automatically stop copying a trader via the monitor's own browser session."""
    ids = TRADER_IDS.get(trader_name)
    if not ids or not ids.get("copy_id"):
        log(f"[AUTO-STOP] {trader_name}: no copy_id in TRADER_IDS — add it and restart.", alert=True)
        return
    copy_id = ids["copy_id"]
    body = {"copyPortfolioId": copy_id}
    for path in [
        "/bapi/futures/v1/private/future/spot-copy-trade/copy-portfolio/stop-copy",
        "/bapi/futures/v1/private/future/spot-copy-trade/copy-portfolio/close",
    ]:
        try:
            data = _trade_post(path, body)
            code = data.get("code", "?") if isinstance(data, dict) else "?"
            if code == "000000":
                log(f"[AUTO-STOP] {trader_name}: stopped successfully.", alert=True)
                ACTIVE_COPIES.pop(trader_name, None)
                return
            log(f"[AUTO-STOP] {path} -> {code}: {data.get('message','') if isinstance(data, dict) else data}")
        except Exception as e:
            log(f"[AUTO-STOP] {path} error: {e}")
    log(f"[AUTO-STOP] {trader_name}: all endpoints failed — MANUAL action required!", alert=True)
    beep(8)


def execute_start(trader_name: str, lead_portfolio_id: str, amount_usdt: float) -> None:
    """Automatically start copying a trader via the monitor's own browser session."""
    attempts = [
        ("/bapi/futures/v1/private/future/spot-copy-trade/copy-portfolio/start-copy", {
            "leadPortfolioId": lead_portfolio_id,
            "investAmount": str(int(amount_usdt)),
            "profitSharingRatio": 10,
        }),
        ("/bapi/futures/v1/private/future/spot-copy-trade/copy-portfolio/create", {
            "leadPortfolioId": lead_portfolio_id,
            "investAmount": str(int(amount_usdt)),
            "profitSharingRatio": 10,
        }),
    ]
    for path, body in attempts:
        try:
            data = _trade_post(path, body)
            code = data.get("code", "?") if isinstance(data, dict) else "?"
            if code == "000000":
                log(f"[AUTO-START] {trader_name}: started successfully ({amount_usdt} USDT).", alert=True)
                ACTIVE_COPIES[trader_name] = amount_usdt
                if trader_name not in TRADER_IDS:
                    TRADER_IDS[trader_name] = {"lead_id": lead_portfolio_id, "copy_id": ""}
                log(f"[AUTO-START] Add TRADER_IDS['{trader_name}']['copy_id'] for future auto-stop.", alert=True)
                return
            log(f"[AUTO-START] {path} -> {code}: {data.get('message','') if isinstance(data, dict) else data}")
        except Exception as e:
            log(f"[AUTO-START] {path} error: {e}")
    log(f"[AUTO-START] {trader_name}: all endpoints failed — MANUAL action required!", alert=True)
    beep(8)


# ---------------------------------------------------------------------------
# Logging / alerts
# ---------------------------------------------------------------------------

def log(msg: str, alert: bool = False) -> None:
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = "*** ALERT *** " if alert else ""
    line   = f"[{ts}] {prefix}{msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def beep(times: int = 3) -> None:
    for _ in range(times):
        winsound.Beep(1000, 400)
        time.sleep(0.15)


# ---------------------------------------------------------------------------
# Browser / Playwright helpers
# ---------------------------------------------------------------------------

def _get_page():
    """Return the live Playwright page, (re)creating browser if needed."""
    global _pw, _browser, _page

    # Check whether existing page is still alive
    if _page is not None:
        try:
            _page.url          # raises if page is closed
            return _page
        except Exception:
            _page = None

    from playwright.sync_api import sync_playwright

    if _pw is None:
        _pw = sync_playwright().start()

    os.makedirs(DATA_DIR, exist_ok=True)
    _browser = _pw.chromium.launch_persistent_context(
        DATA_DIR,
        headless=False,
        args=["--no-first-run", "--disable-popup-blocking",
              "--disable-notifications"],
    )

    if _browser.pages:
        _page = _browser.pages[0]
    else:
        _page = _browser.new_page()

    url = _page.url or ""
    if "binance.com/en/copy-trading" not in url:
        log("Opening Binance copy trading page…")
        try:
            _page.goto("https://www.binance.com/en/copy-trading/spot",
                       wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass

    # Give Binance's React time to boot and potentially redirect to login
    import time as _t
    _t.sleep(8)
    current = _page.url or ""

    # If not on the copy-trading page, user needs to log in
    if "copy-trading" not in current:
        log("Not on copy trading page (currently: {current[:60]}) — login needed.")
        log("Please log in in the Chromium browser window that opened.")
        log("Waiting up to 3 minutes for login to complete…")
        _page.wait_for_url("**/copy-trading/**", timeout=180_000)
        log("Login detected — waiting for page to fully load…")
        _t.sleep(5)

    # Final wait for the page's network to settle before we start fetching
    try:
        _page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass  # networkidle timeout is acceptable

    log(f"Page ready at: {_page.url[:80]}")
    return _page


def _reset_browser() -> None:
    """Close the browser so the next poll restarts it cleanly."""
    global _browser, _page
    try:
        if _browser:
            _browser.close()
    except Exception:
        pass
    _browser = None
    _page    = None


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

_FETCH_JS = """
async (args) => {
    const r = await fetch(args.endpoint, {
        method:  "POST",
        headers: {"Content-Type": "application/json",
                  "clienttype": "web", "lang": "en"},
        body:    JSON.stringify(args.payload)
    });
    return await r.json();
}
"""


def fetch_traders() -> list:
    page        = _get_page()
    all_traders = []

    for page_num in range(1, 10):          # up to ~450 traders (9 pages × 50)
        payload = {**BASE_PAYLOAD, "pageNumber": page_num}
        try:
            data = page.evaluate(_FETCH_JS, {"endpoint": ENDPOINT, "payload": payload})
        except Exception as e:
            log(f"[Fetch] Error on page {page_num}: {e} — resetting browser.")
            _reset_browser()
            break

        lst = (data or {}).get("data", {}).get("list") or []
        if not lst:
            break

        all_traders.extend(lst)

        total = int((data or {}).get("data", {}).get("total") or 0)
        if total and len(all_traders) >= total:
            break

    log(f"Fetched {len(all_traders)} traders.")
    return all_traders


# ---------------------------------------------------------------------------
# Trader normalisation
# ---------------------------------------------------------------------------

def parse_trader(t: dict) -> dict:
    pid = str(t.get("leadPortfolioId") or "")
    cop = int(t.get("currentCopyCount") or 0)
    mx  = int(t.get("finalEffectiveMaxCopyCount") or t.get("maxCopyCount") or 300)
    roi = float(t.get("roi")       or 0)
    mdd = float(t.get("mdd")       or 99)
    days  = int(t.get("tradingDays") or 0)
    sharpe = float(t.get("sharpRatio")  or 0)
    pnl    = float(t.get("pnl")         or 0)
    win_rate = float(t.get("winRate") or t.get("followerWinRate") or 0)

    return {
        "name":        t.get("nickname") or "Unknown",
        "pid":         pid,
        "copiers":     cop,
        "max_copiers": mx,
        "slots_free":  max(0, mx - cop),
        "days":        days,
        "roi_30":      roi,
        "mdd":         mdd,
        "sharpe":      sharpe,
        "pnl_30":      pnl,
        "win_rate":    win_rate,
        "url": f"https://www.binance.com/en/copy-trading/lead-details/{pid}?timeRange=30D",
    }


# ---------------------------------------------------------------------------
# Scoring (0-100)
# ---------------------------------------------------------------------------

def score(p: dict) -> int:
    s = 0
    # Core quality
    if p["mdd"]    <= 5.0:          s += 40   # very low drawdown = best signal
    elif p["mdd"]  <= MAX_MDD:      s += 25
    if p["roi_30"] >= 20.0:         s += 30
    elif p["roi_30"] >= MIN_ROI_30D: s += 15
    if p["days"]   >= 90:           s += 15
    elif p["days"] >= MIN_DAYS:     s += 8
    if p["sharpe"] >= 1.0:          s += 10
    elif p["sharpe"] >= MIN_SHARPE: s += 5
    if p["win_rate"] >= 70.0:       s += 10
    elif p["win_rate"] >= MIN_WIN_RATE: s += 5
    if p["pnl_30"] >= 0:            s += 5
    # Penalties
    if p["roi_30"] > MAX_ROI_30D:   s -= 25   # martingale / extreme leverage
    if p["mdd"]    > MAX_MDD:       s -= 30
    if p["sharpe"] < 0:             s -= 10
    if p["slots_free"] < MIN_SLOTS: s -= 20   # full — can't enter
    return max(0, min(100, s))


def _is_already_copying(name: str) -> bool:
    return any(name.lower() in k.lower() or k.lower() in name.lower()
               for k in ACTIVE_COPIES)


def _total_deployed() -> float:
    return sum(ACTIVE_COPIES.values())


# ---------------------------------------------------------------------------
# Check active copies + auto-discovery
# ---------------------------------------------------------------------------

def check(traders: list) -> None:
    parsed  = [parse_trader(t) for t in traders]
    by_name = {p["name"]: p for p in parsed}

    # ── 1. Exit checks for active copies ────────────────────────────────────
    for active_name, allocated in list(ACTIVE_COPIES.items()):
        match = next(
            (p for n, p in by_name.items() if active_name.lower() in n.lower()), None
        )
        if not match:
            log(f"[EXIT] {active_name}: vanished from leaderboard — may have stopped trading!", alert=True)
            beep(4)
            continue

        cap   = allocated or CAPITAL_PER_TRADER
        pnl_e = cap * (match["roi_30"] / 100)
        mdd_e = cap * (match["mdd"]    / 100)
        snap  = _entry_snapshots.get(active_name, {})

        # Update peak ROI tracking
        if snap:
            if match["roi_30"] > snap.get("roi_peak", match["roi_30"]):
                _entry_snapshots[active_name]["roi_peak"] = match["roi_30"]
        else:
            # First time we see this active trader — record baseline
            _entry_snapshots[active_name] = {
                "mdd_at_entry": match["mdd"],
                "roi_at_entry": match["roi_30"],
                "roi_peak":     match["roi_30"],
            }
            snap = _entry_snapshots[active_name]

        roi_peak  = snap.get("roi_peak", match["roi_30"])
        mdd_entry = snap.get("mdd_at_entry", match["mdd"])

        # Determine exit reason
        exit_reason = None
        if match["mdd"] > EXIT_MDD:
            exit_reason = f"MDD {match['mdd']:.1f}% > hard limit {EXIT_MDD}%"
        elif match["roi_30"] < EXIT_ROI_FLOOR:
            exit_reason = f"30D ROI {match['roi_30']:.1f}% < floor {EXIT_ROI_FLOOR}%"
        elif (match["mdd"] - mdd_entry) > EXIT_MDD_SPIKE:
            exit_reason = f"MDD spiked +{match['mdd']-mdd_entry:.1f}% since entry (was {mdd_entry:.1f}% → now {match['mdd']:.1f}%)"
        elif (roi_peak - match["roi_30"]) > EXIT_ROI_DROP:
            exit_reason = f"ROI dropped {roi_peak-match['roi_30']:.1f}% from peak {roi_peak:.1f}% → {match['roi_30']:.1f}%"

        if exit_reason:
            key = f"exit_{match['name']}_{exit_reason[:20]}"
            if key not in previous_alerts:
                previous_alerts.add(key)
                log(
                    f"[AUTO-EXIT] {active_name}: {exit_reason}\n"
                    f"  Capital at risk: ${mdd_e:.2f} of ${cap:.0f}. STOPPING NOW.",
                    alert=True,
                )
                beep(6)
                execute_stop(active_name)
        else:
            proj_daily  = cap * (match["roi_30"] / 100) / 30
            proj_weekly = proj_daily * 7
            log(
                f"[HOLDING] {active_name}: ROI {match['roi_30']:.1f}%  MDD {match['mdd']:.1f}%  "
                f"Sharpe {match['sharpe']:.2f}  WinRate {match['win_rate']:.0f}%  "
                f"~${proj_daily:+.2f}/day  ~${proj_weekly:+.2f}/wk  Score:{score(match)}"
            )

    # ── 2. Daily P&L estimate vs $5 target ──────────────────────────────────
    total_daily = sum(
        (ACTIVE_COPIES.get(n, CAPITAL_PER_TRADER) *
         (next((p["roi_30"] for name, p in by_name.items() if n.lower() in name.lower()), 0) / 100) / 30)
        for n in ACTIVE_COPIES
    )
    gap = DAILY_TARGET - total_daily
    log(
        f"[P&L] Est daily across {len(ACTIVE_COPIES)} trader(s): ${total_daily:.2f}  "
        f"Target: ${DAILY_TARGET:.2f}/day  Gap: ${gap:+.2f}  "
        f"Total deployed: ${_total_deployed():.0f}/${MAX_DEPLOYED:.0f}"
    )

    # ── 3. Auto-entry: find best qualifying traders not yet copied ───────────
    slots_available = MAX_COPIES - len(ACTIVE_COPIES)
    budget_remaining = MAX_DEPLOYED - _total_deployed()

    if slots_available > 0 and budget_remaining >= CAPITAL_PER_TRADER:
        candidates = sorted(
            [
                (score(p), p["name"], p)
                for p in parsed
                if (
                    p["mdd"]       <= MAX_MDD
                    and MIN_ROI_30D <= p["roi_30"] <= MAX_ROI_30D
                    and p["days"]  >= MIN_DAYS
                    and p["sharpe"] >= MIN_SHARPE
                    and p["slots_free"] >= MIN_SLOTS
                    and (p["win_rate"] == 0 or p["win_rate"] >= MIN_WIN_RATE)
                    and score(p)   >= MIN_SCORE
                    and not _is_already_copying(p["name"])
                )
            ],
            reverse=True,
        )

        entered = 0
        for sc, _, p in candidates:
            if entered >= slots_available:
                break
            if _total_deployed() + CAPITAL_PER_TRADER > MAX_DEPLOYED:
                break
            key = f"autoenter_{p['pid']}"
            if key in previous_alerts:
                continue
            previous_alerts.add(key)
            proj_daily = CAPITAL_PER_TRADER * (p["roi_30"] / 100) / 30
            log(
                f"[AUTO-ENTER] {p['name']} (score {sc}/100)\n"
                f"  ROI:{p['roi_30']:.1f}%  MDD:{p['mdd']:.1f}%  Days:{p['days']}  "
                f"Sharpe:{p['sharpe']:.2f}  WinRate:{p['win_rate']:.0f}%  Slots:{p['slots_free']}\n"
                f"  Deploying ${CAPITAL_PER_TRADER:.0f} → est ${proj_daily:.2f}/day",
                alert=True,
            )
            beep(3)
            execute_start(p["name"], p["pid"], CAPITAL_PER_TRADER)
            entered += 1

        if not candidates:
            # Show top 5 closest to qualifying so you can see what's near the threshold
            top5 = sorted(parsed, key=score, reverse=True)[:5]
            log("  No auto-enter candidates this poll. Top 5 by score:")
            for p in top5:
                log(f"    {p['name']:25} ROI:{p['roi_30']:6.1f}%  MDD:{p['mdd']:5.1f}%  "
                    f"Days:{p['days']:4}  Score:{score(p):3}  Slots:{p['slots_free']}")
    else:
        reason = f"slots full ({len(ACTIVE_COPIES)}/{MAX_COPIES})" if slots_available <= 0 \
                 else f"budget exhausted (${_total_deployed():.0f}/${MAX_DEPLOYED:.0f})"
        log(f"  Auto-enter paused: {reason}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    log("=== Binance Copy Monitor starting ===")
    log(f"  Goal: ${DAILY_TARGET:.2f}/day | Max {MAX_COPIES} traders | Max ${MAX_DEPLOYED:.0f} deployed")
    log(f"  Entry:  MDD<={MAX_MDD}%  ROI {MIN_ROI_30D}-{MAX_ROI_30D}%  Days>={MIN_DAYS}  Score>={MIN_SCORE}")
    log(f"  Exit:   MDD>{EXIT_MDD}%  ROI<{EXIT_ROI_FLOOR}%  MDD spike>{EXIT_MDD_SPIKE}%  ROI drop>{EXIT_ROI_DROP}%")
    if ACTIVE_COPIES:
        log(f"  Active: { {k: f'${v:.0f}' for k,v in ACTIVE_COPIES.items()} }")

    poll_count = 0
    while True:
        poll_count += 1
        log(f"--- Poll #{poll_count} ---")
        try:
            traders = fetch_traders()
            if traders:
                check(traders)
            else:
                log("WARNING: no traders returned this poll.")
        except Exception as e:
            log(f"ERROR during poll: {e}")

        log(f"Sleeping {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
