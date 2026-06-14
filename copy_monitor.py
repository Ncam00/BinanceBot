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
import re
import time
import winsound
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WATCHLIST     = {}   # manual watchlist (auto-discovery now handles entry)

# Traders you are CURRENTLY copying. Updated automatically on auto-start/stop.
# Source of truth is active_copies.json (loaded at startup); this is only the
# fallback seed if that file is missing.
ACTIVE_COPIES = {
    "低调交易员": 125.0,
}   # name -> USDT allocated

# ---- Position sizing -------------------------------------------------------
CAPITAL_PER_TRADER = 125.0   # base USDT per trader
MAX_COPIES         = 4        # never hold more than this many at once
MAX_DEPLOYED       = 500.0    # hard cap on total USDT deployed across all copies
DAILY_TARGET       = 5.0      # daily profit goal (aligned with dashboard)

# ---- Entry thresholds (auto-start when ALL met) ----------------------------
MIN_DAYS      = 30     # ≥30 days history (relaxed to find more candidates)
MAX_MDD       = 15.0   # max drawdown %
MIN_ROI_30D   = 15.0   # minimum 30-day ROI % (quality gate)
MIN_ROI_7D    = 0.0    # minimum 7-day ROI % (momentum gate — must not be losing this week)
MAX_ROI_30D   = 150.0  # cap — filters obvious martingale blow-ups
MIN_WIN_RATE  = 55.0   # minimum win rate % (if available)
MIN_SHARPE    = 1.2    # minimum Sharpe ratio (raised for more consistent picks)
MIN_SLOTS     = 1      # must have at least 1 free slot
MIN_SCORE     = 65     # minimum composite score (0-100)
REQUIRE_ROI_7D = True   # Require a positive recent-form signal to enter. Uses
                        # official 7D ROI when the trader is in the 7D top-list,
                        # else the chartItems momentum slope (rising last 7 days).
                        # Only blocks when BOTH signals are missing.

# ---- Exit thresholds (auto-stop when ANY hit) ------------------------------
EXIT_MDD           = 20.0   # hard exit: MDD crosses this
EXIT_ROI_FLOOR     = -5.0   # hard exit: 30D ROI goes negative beyond this
EXIT_MDD_SPIKE     = 7.0    # exit if MDD rises by this much since we started copying
EXIT_ROI_DROP      = 10.0   # exit if ROI drops by this much since peak

# ---- Auto-rotation (upgrade weak idle holdings without a deposit) -----------
# When at capacity and free balance is too low to add a new slot, swap an
# IDLE, low-scoring holding for a much stronger available trader. Stopping the
# weak copy frees its USDT, which funds the replacement — net deployment and
# slot count are unchanged. Guardrails keep this conservative:
ENABLE_ROTATION       = True
ROTATION_SCORE_MARGIN = 12        # candidate must beat the held trader by ≥ this many points
ROTATION_COOLDOWN     = 6 * 3600  # don't rotate the same slot more than once per 6 h
ROTATION_FLAT_BAND    = 1.0       # only rotate when held REAL P&L is within ±$ this (idle/flat)

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


def _persist_trader_ids() -> None:
    """Save TRADER_IDS to disk so copy_ids survive restarts."""
    try:
        import json as _j
        with open(TRADER_IDS_FILE, "w", encoding="utf-8") as f:
            _j.dump(TRADER_IDS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Could not persist TRADER_IDS: {e}")


def _load_trader_ids() -> None:
    """Merge any saved trader IDs from disk into TRADER_IDS."""
    try:
        import json as _j
        if os.path.exists(TRADER_IDS_FILE):
            with open(TRADER_IDS_FILE, encoding="utf-8") as f:
                saved = _j.load(f)
            for name, info in saved.items():
                if name not in TRADER_IDS or not TRADER_IDS[name].get("copy_id"):
                    TRADER_IDS[name] = info
    except Exception as e:
        print(f"[WARN] Could not load TRADER_IDS: {e}")


def _persist_active_copies() -> None:
    """Save ACTIVE_COPIES to disk so live positions survive restarts."""
    try:
        import json as _j
        with open(ACTIVE_COPIES_FILE, "w", encoding="utf-8") as f:
            _j.dump(ACTIVE_COPIES, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Could not persist ACTIVE_COPIES: {e}")


def _load_active_copies() -> None:
    """Load active copies from disk (source of truth across restarts).
    If no file exists yet, seed it from the hardcoded defaults."""
    try:
        import json as _j
        if os.path.exists(ACTIVE_COPIES_FILE):
            with open(ACTIVE_COPIES_FILE, encoding="utf-8") as f:
                saved = _j.load(f)
            if isinstance(saved, dict):
                ACTIVE_COPIES.clear()
                for name, amt in saved.items():
                    try:
                        ACTIVE_COPIES[name] = float(amt)
                    except Exception:
                        continue
        else:
            _persist_active_copies()
    except Exception as e:
        print(f"[WARN] Could not load ACTIVE_COPIES: {e}")

# NOTE: entry/exit thresholds are defined once in the config block above.
# The previous duplicate definitions here silently lowered MIN_ROI_30D to 8%
# — removed so the intended 15% quality gate actually applies.

POLL_INTERVAL = 300    # seconds between polls (5 min)
LOG_FILE      = r"C:\BinanceBot\copy_alerts.log"
DATA_DIR      = r"C:\BinanceBot\playwright_session"   # logged-in Chrome profile
COOKIE_FILE   = r"C:\BinanceBot\session_cookies.json" # backup cookies
TRADER_IDS_FILE = r"C:\BinanceBot\trader_ids.json"     # persisted copy_ids
ACTIVE_COPIES_FILE = r"C:\BinanceBot\active_copies.json"  # persisted live positions
SCREENSHOT_DIR = r"C:\BinanceBot\screenshots"          # UI-action proof shots
BLACKLIST_FILE = r"C:\BinanceBot\blacklist.json"        # traders we cannot copy (private/error)
REAL_PNL_FILE  = r"C:\BinanceBot\real_pnl.json"         # latest REAL copier P&L snapshot (for dashboard)

# When True, the bot performs every step of a copy/stop EXCEPT clicking the
# final Confirm button. Use to verify selectors before risking real money.
DRY_RUN = False

# Names of traders we have permanently skipped (private, errored, manually banned)
BLACKLIST: set = set()


def _load_blacklist() -> None:
    try:
        import json as _j
        if os.path.exists(BLACKLIST_FILE):
            with open(BLACKLIST_FILE, encoding="utf-8") as f:
                BLACKLIST.update(_j.load(f) or [])
    except Exception as e:
        print(f"[WARN] Could not load BLACKLIST: {e}")


def _persist_blacklist() -> None:
    try:
        import json as _j
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            _j.dump(sorted(BLACKLIST), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Could not persist BLACKLIST: {e}")


def _blacklist_trader(name: str, reason: str) -> None:
    if name in BLACKLIST:
        return
    BLACKLIST.add(name)
    _persist_blacklist()
    # log() is defined later in the file; guard for early calls
    try:
        log(f"[BLACKLIST] {name} added ({reason}).", alert=True)
    except Exception:
        print(f"[BLACKLIST] {name} added ({reason}).")


# Per-trader cooldown after an insufficient-balance abort, so the bot stops
# retrying a copy it can't fund every single poll. {name: epoch_seconds_until}.
_INSUFFICIENT_FUNDS_UNTIL: dict = {}
INSUFFICIENT_FUNDS_COOLDOWN = 3600  # 1 hour

# Last time each slot was rotated (name -> epoch), to throttle auto-rotation.
_LAST_ROTATION: dict = {}

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

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

# JS to check auth — checks ALL known Binance auth cookies, then falls back
# to a real authenticated API call. Binance uses different cookie names across
# regions/redesigns (p20t, cr00, bnc-uuid, BNC_FV_KEY_T, p20t_d).
_AUTH_CHECK_JS = """
async () => {
    const authCookies = ['p20t', 'cr00', 'p20t_d', 'BNC_FV_KEY_T', 'aws-waf-token'];
    const cookies = document.cookie.split(';').map(c => c.trim().split('=')[0]);
    if (authCookies.some(name => cookies.includes(name))) return true;
    try {
        const r = await fetch(
            'https://www.binance.com/bapi/accounts/v1/private/account/user/base-detail',
            {credentials:'include', headers:{'content-type':'application/json','clienttype':'web'}}
        );
        const d = await r.json();
        return d && d.code === '000000';
    } catch(e) { return false; }
}
"""


def _check_auth() -> bool:
    """Return True if the current browser session is authenticated with Binance."""
    page = _get_page()
    try:
        return bool(page.evaluate(_AUTH_CHECK_JS))
    except Exception:
        return False


# Once login fails this poll, skip further attempts until next poll cycle
_login_failed_this_poll: bool = False
_consecutive_auth_failures: int = 0
SESSION_FLAG_FILE = r"C:\BinanceBot\SESSION_EXPIRED.flag"


def _ensure_logged_in() -> bool:
    """If not logged in, navigate to Binance login and wait up to 5 minutes."""
    global _login_failed_this_poll
    if _check_auth():
        return True
    if _login_failed_this_poll:
        return False
    page = _get_page()
    log("Browser session not authenticated — opening Binance login page…", alert=True)
    beep(5)
    try:
        page.goto("https://accounts.binance.com/en/login",
                  wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        pass
    log(">>> PLEASE LOG IN TO BINANCE IN THE BROWSER WINDOW. Waiting up to 5 minutes… <<<", alert=True)
    try:
        page.wait_for_function(
            "() => !window.location.hostname.includes('accounts.binance.com')",
            timeout=300_000,
        )
        log("Login detected — waiting for session cookies to settle (15s)…")
        time.sleep(15)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        for attempt in range(3):
            if _check_auth():
                log("Login confirmed — bot resuming automated trading.", alert=True)
                _save_cookies()
                beep(3)
                return True
            if attempt < 2:
                log(f"Auth check attempt {attempt+1} failed, retrying in 5s…")
                time.sleep(5)
        log("Auth still failing after login. Check the browser for security/2FA challenge.")
    except Exception:
        log("Login wait timed out (5 min). Will retry on next poll.")
    _login_failed_this_poll = True
    return False


def _record_auth_failure() -> None:
    """Track consecutive auth failures; write flag file after 2 in a row."""
    global _consecutive_auth_failures
    _consecutive_auth_failures += 1
    if _consecutive_auth_failures >= 2:
        try:
            with open(SESSION_FLAG_FILE, "w") as f:
                f.write(f"Session expired at {datetime.now().isoformat()}\n")
        except Exception:
            pass
        log(
            "!!! SESSION EXPIRED — bot cannot trade. "
            "Run: C:\\python314\\python.exe C:\\BinanceBot\\login_helper.py",
            alert=True,
        )


def _clear_auth_failure() -> None:
    global _consecutive_auth_failures
    _consecutive_auth_failures = 0
    try:
        if os.path.exists(SESSION_FLAG_FILE):
            os.remove(SESSION_FLAG_FILE)
    except Exception:
        pass
# JS helper for authenticated BAPI calls (reuses copy_monitor's own page)
_TRADE_JS = """
async (args) => {
    const getCookie = (name) => {
        const m = document.cookie.match('(^|;)\\\\s*' + name + '\\\\s*=\\\\s*([^;]+)');
        return m ? m.pop() : '';
    };
    const headers = {
        'content-type': 'application/json',
        'clienttype': 'web',
        'x-source': 'web',
        'bnc-location': 'BINANCE',
        'lang': 'en',
    };
    const csrf = getCookie('csrftoken') || getCookie('cr00');
    if (csrf) headers['csrftoken'] = csrf;
    const uuid = getCookie('bnc-uuid');
    if (uuid) headers['bnc-uuid'] = uuid;
    const r = await fetch(args.url, {
        method: 'POST',
        credentials: 'include',
        headers: headers,
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
    """POST to Binance BAPI using the monitor's live browser session, with soft retry.

    Soft retry only — never resets the browser here. Closing the persistent
    context mid-session was logging the user out of Binance.
    """
    last_err = None
    for attempt in range(3):
        try:
            page = _get_page()
            return page.evaluate(_TRADE_JS, {"url": f"https://www.binance.com{path}", "body": body})
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2)
    raise last_err if last_err else RuntimeError("trade_post failed")


def _lookup_copy_id(lead_portfolio_id: str) -> str:
    """Find the copy_id for a given lead trader by querying the user's active portfolios."""
    for path in [
        "/bapi/futures/v1/private/future/spot-copy-trade/copy-portfolio/list",
        "/bapi/futures/v1/private/future/spot-copy-trade/copy-portfolio/my-list",
    ]:
        try:
            data = _trade_post(path, {"pageNumber": 1, "pageSize": 100})
            if not isinstance(data, dict) or data.get("code") != "000000":
                continue
            payload = data.get("data") or {}
            items = payload.get("list") or payload.get("portfolios") or []
            for item in items:
                if str(item.get("leadPortfolioId") or "") == str(lead_portfolio_id):
                    cid = str(item.get("copyPortfolioId") or item.get("portfolioId") or item.get("id") or "")
                    if cid:
                        return cid
        except Exception:
            pass
    return ""


def _screenshot(label: str, full_page: bool = True, page=None) -> str:
    """Save a timestamped screenshot of the current page; returns the path."""
    try:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(SCREENSHOT_DIR, f"{label}_{ts}.png")
        p = page if page is not None else _get_page()
        p.screenshot(path=path, full_page=full_page)
        return path
    except Exception as e:
        log(f"[screenshot] failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Real copy-portfolio P&L (reads YOUR actual positions, not lead estimates)
# ---------------------------------------------------------------------------

# Candidate field names Binance uses for copier PnL / principal / roi.
# Confirmed live fields (user-spot-copy-detail-list): realizedPnl, unrealizedPnl,
# netProfit, copyBalance, currentAvailableAmount, leadNickName. Scanned in
# priority order with a fuzzy contains-match fallback for resilience.
_PNL_FIELD_KEYS = [
    "netProfit", "totalCopyPnl", "copyPnl", "totalPnl", "totalProfit",
    "totalIncome", "income", "profit", "pnl",
]
_PRINCIPAL_FIELD_KEYS = [
    "copyBalance", "currentAvailableAmount", "totalCopyAmount", "copyAmount",
    "marginBalance", "initInvestAsset", "investAmount", "principal",
    "totalAmount", "amount",
]
_ROI_FIELD_KEYS = ["copyRoi", "totalRoi", "roiValue", "roi"]


def _coerce_float(v):
    try:
        if v in (None, ""):
            return None
        return float(v)
    except Exception:
        return None


def _extract_pnl_fields(item: dict) -> dict:
    """Defensively pull PnL / principal / ROI numbers from a portfolio item."""
    out = {"pnl": None, "principal": None, "roi": None}

    # Preferred: true total P&L = realized + unrealized (confirmed fields).
    realized = _coerce_float(item.get("realizedPnl"))
    unrealized = _coerce_float(item.get("unrealizedPnl"))
    if realized is not None or unrealized is not None:
        out["pnl"] = (realized or 0.0) + (unrealized or 0.0)

    for keys, dest in ((_PNL_FIELD_KEYS, "pnl"),
                       (_PRINCIPAL_FIELD_KEYS, "principal"),
                       (_ROI_FIELD_KEYS, "roi")):
        if out[dest] is not None:
            continue
        for k in keys:
            if k in item:
                val = _coerce_float(item[k])
                if val is not None:
                    out[dest] = val
                    break
    # Fuzzy fallback for PnL if no known key matched
    if out["pnl"] is None:
        for k, v in item.items():
            kl = str(k).lower()
            if ("pnl" in kl or "profit" in kl or "income" in kl) and "rate" not in kl:
                val = _coerce_float(v)
                if val is not None:
                    out["pnl"] = val
                    break
    # Derive ROI from pnl/principal when not provided directly.
    if out["roi"] is None and out["pnl"] is not None and out["principal"]:
        try:
            out["roi"] = out["pnl"] / out["principal"] * 100
        except Exception:
            pass
    return out


def _parse_copy_items(items, result: dict) -> None:
    """Parse a list of copy-portfolio items into the result dict (in place)."""
    for item in items:
        if not isinstance(item, dict):
            continue
        # Only real position rows carry balance/PnL fields. Skip noise endpoints
        # like get-lead-slot-reminder (nickname/leadPortfolioId only).
        if not any(k in item for k in
                   ("copyBalance", "realizedPnl", "unrealizedPnl", "netProfit")):
            continue
        # Skip CLOSED/ENDED copies. The detail-list returns full history; a
        # finished copy has a truthy endTime and/or a closedReason set.
        status = item.get("portfolioStatus") or item.get("status")
        if isinstance(status, str) and status.upper() in (
                "CLOSED", "ENDED", "STOP", "STOPPED", "FINISHED"):
            continue
        end_time = item.get("endTime")
        try:
            if end_time and float(end_time) > 0:
                continue
        except Exception:
            pass
        if item.get("closedReason"):
            continue
        name = (item.get("leadNickName") or item.get("nickname")
                or item.get("leadNickname") or item.get("name") or "")
        fields = _extract_pnl_fields(item)
        entry = {
            **fields,
            "lead_id": str(item.get("leadPortfolioId") or ""),
            "copy_id": str(item.get("copyPortfolioId")
                           or item.get("portfolioId") or item.get("id") or ""),
            "name": name,
        }
        key = name or entry["copy_id"]
        if key:
            result[key] = entry


def _fetch_my_copy_positions() -> dict:
    """Return {name: {pnl, principal, roi, lead_id, copy_id, name}} for YOUR
    real ongoing copy portfolios. Empty dict if unauthenticated/unavailable.

    The futures-private BAPI rejects hand-crafted fetches ("Please log in
    first") because the SPA attaches signing headers we can't reproduce. So
    instead we capture the copy-portfolio responses the page itself fires when
    we open the "my copies" page, and parse those.
    """
    if not _check_auth():
        return {}
    result: dict = {}
    captured: list = []

    def _on_response(resp):
        try:
            url = resp.url
            if resp.status == 200 and ("copy-portfolio" in url
                                       or "copy-detail" in url
                                       or "detail-list" in url):
                captured.append(resp)
        except Exception:
            pass

    def _has_detail(resp_list):
        """True once the real position detail-list response is captured."""
        for r in resp_list:
            if "detail-list" in r.url or "copy-detail" in r.url:
                return True
        return False

    page = _get_page()
    page.on("response", _on_response)
    try:
        for url in (
            "https://www.binance.com/en/copy-trading/copy-management?biz=spot",
            "https://www.binance.com/en/copy-trading/spot?tab=mine",
        ):
            try:
                page.goto(url, wait_until="networkidle", timeout=25000)
            except Exception:
                continue
            # Detect a logged-out redirect to the accounts/login domain.
            if "accounts.binance.com" in (page.url or ""):
                if not getattr(_fetch_my_copy_positions, "_warned_logout", False):
                    log("[REAL-PNL] Binance session is logged out for private "
                        "actions — please log in again in the bot's browser "
                        "window. Real P&L and auto-entry are blocked until then.",
                        alert=True)
                    _fetch_my_copy_positions._warned_logout = True
                    _record_auth_failure()
                return {}
            try:
                page.wait_for_timeout(4000)
            except Exception:
                pass
            # Only stop once we have the real detail-list (not just noise XHRs).
            if _has_detail(captured):
                break
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass

    # A successful read means the private session works again — clear warning.
    _fetch_my_copy_positions._warned_logout = False

    for resp in captured:
        try:
            data = resp.json()
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        tail = resp.url.split("/")[-1].split("?")[0]
        code = data.get("code")
        payload = data.get("data") or {}
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("list") or payload.get("portfolios") or []
        else:
            items = []
        # Only the detail-list carries real positions; ignore other XHRs.
        if code == "000000" and items and ("detail-list" in resp.url
                                           or "copy-detail" in resp.url):
            _parse_copy_items(items, result)

    return result


def _persist_real_pnl(positions: dict, total_pnl: float, total_principal: float) -> None:
    """Write the latest real-PnL snapshot to disk for the dashboard."""
    try:
        import json as _j
        snap = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "total_pnl": round(total_pnl, 4),
            "total_principal": round(total_principal, 4),
            "positions": {
                k: {kk: vv for kk, vv in v.items() if kk in ("pnl", "principal", "roi")}
                for k, v in positions.items()
            },
        }
        with open(REAL_PNL_FILE, "w", encoding="utf-8") as f:
            _j.dump(snap, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"[REAL-PNL] persist failed: {e}")


def _click_first_visible(page, selectors, timeout_each_ms: int = 4000) -> bool:
    """Try a list of selectors; click the first visible one. Return True on click."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_each_ms)
            loc.click()
            return True
        except Exception:
            continue
    return False


def _capture_copy_id_from_response(page, lead_id: str, timeout_ms: int = 8000) -> str:
    """Listen for any /copy-portfolio response after a click and pull copy_id."""
    try:
        with page.expect_response(
            lambda r: "spot-copy-trade/copy-portfolio" in r.url and r.status == 200,
            timeout=timeout_ms,
        ) as resp_info:
            pass
        resp = resp_info.value
        try:
            data = resp.json()
        except Exception:
            return ""
        payload = (data or {}).get("data") or {}
        items = payload if isinstance(payload, list) else (
            payload.get("list") or payload.get("portfolios") or [payload]
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("leadPortfolioId") or "") == str(lead_id) or not lead_id:
                cid = str(
                    item.get("copyPortfolioId")
                    or item.get("portfolioId")
                    or item.get("id")
                    or ""
                )
                if cid:
                    return cid
        return ""
    except Exception:
        return ""


# Selector pools — try multiple in case Binance tweaks the DOM.
# Use EXACT text match so we don't accidentally hit "Mock Copy" or "Copy Now".
_COPY_BUTTON_SELECTORS = [
    'button:text-is("Copy")',
    'div[role="button"]:text-is("Copy")',
    '[data-bn-type="button"]:text-is("Copy")',
    'button:text-is("复制")',
]

# Phrases that indicate the trader cannot be copied normally — bot blacklists.
_PRIVATE_MARKERS = [
    "verify invitation code",
    "invitation code",
    "this portfolio is private",
    "private portfolio",
]
_AMOUNT_INPUT_SELECTORS = [
    'div[role="dialog"] input[placeholder*="amount" i]',
    'div[role="dialog"] input[placeholder*="USDT" i]',
    'div[role="dialog"] input[inputmode="decimal"]',
    'div[role="dialog"] input[type="number"]',
    '[class*="odal"] input[inputmode="decimal"]',
    '[class*="odal"] input[type="number"]',
    '[class*="rawer"] input[inputmode="decimal"]',
    '[class*="rawer"] input[type="number"]',
    'input[placeholder*="amount" i]',
    'input[placeholder*="USDT" i]',
    'input[inputmode="decimal"]',
    'input[type="number"]',
]
_CONFIRM_BUTTON_SELECTORS = [
    'button:has-text("Confirm")',
    'button:has-text("Copy Now")',
    'button:has-text("Start Copy")',
    'button:has-text("确认")',
    '[data-bn-type="button"]:has-text("Confirm")',
]
_AGREE_CHECKBOX_SELECTORS = [
    'input[type="checkbox"]',
    'div[role="checkbox"]',
]
_STOP_BUTTON_SELECTORS = [
    'button:has-text("Stop Copying")',
    'button:has-text("Stop")',
    'button:has-text("Close")',
    'button:has-text("结束跟单")',
]


def execute_start(trader_name: str, lead_portfolio_id: str, amount_usdt: float) -> None:
    """Start copying a trader by driving the Binance UI directly."""
    if not _ensure_logged_in():
        log(f"[AUTO-START] {trader_name}: not authenticated — skipping.", alert=True)
        return

    # Skip if this trader is in an insufficient-funds cooldown.
    until = _INSUFFICIENT_FUNDS_UNTIL.get(trader_name, 0)
    if until and time.time() < until:
        mins = int((until - time.time()) / 60)
        log(f"[AUTO-START] {trader_name}: skipping — insufficient free balance "
            f"(cooldown {mins} min remaining). Free up USDT to fund this copy.")
        return

    # Hard cap as a safety net regardless of caller
    if amount_usdt > CAPITAL_PER_TRADER:
        log(f"[AUTO-START] {trader_name}: amount {amount_usdt} > cap {CAPITAL_PER_TRADER} — clamping.", alert=True)
        amount_usdt = CAPITAL_PER_TRADER

    page = _get_page()
    detail_url = f"https://www.binance.com/en/copy-trading/lead-details/{lead_portfolio_id}?timeRange=30D"
    log(f"[AUTO-START] {trader_name}: opening {detail_url}")
    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        log(f"[AUTO-START] {trader_name}: navigation failed: {e}", alert=True)
        return
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass

    # Pre-check: detect "Private" badge on the page → blacklist & abort
    try:
        page_text_pre = (page.content() or "").lower()
    except Exception:
        page_text_pre = ""
    if any(m in page_text_pre for m in _PRIVATE_MARKERS):
        _screenshot(f"start_skip_private_{trader_name}")
        _blacklist_trader(trader_name, "private/invitation-only")
        return

    # Step 1 — click "Copy" on the lead-details page (use role-based + exact-name)
    # Watch for popups/new tabs (Binance may open the copy form in a new page)
    popup_holder: dict = {"page": None}
    def _on_popup(p):
        popup_holder["page"] = p
    page.on("popup", _on_popup)
    ctx_before = page.context
    pages_before = len(ctx_before.pages)
    url_before = ""
    try:
        url_before = page.url
    except Exception:
        pass

    clicked = False
    try:
        btn = page.get_by_role("button", name="Copy", exact=True).first
        btn.wait_for(state="visible", timeout=8000)
        btn.scroll_into_view_if_needed()
        btn.click()
        clicked = True
    except Exception:
        pass
    if not clicked:
        clicked = _click_first_visible(page, _COPY_BUTTON_SELECTORS, timeout_each_ms=6000)
    if not clicked:
        log(f"[AUTO-START] {trader_name}: 'Copy' button not found.", alert=True)
        _screenshot(f"start_fail_copybtn_{trader_name}")
        beep(5)
        return

    # Step 2 — wait for modal/navigation/popup to settle
    time.sleep(3.0)
    try:
        page.wait_for_selector('div[role="dialog"], [class*="odal"], [class*="rawer"]', timeout=6000)
    except Exception:
        pass

    # If a new tab was opened, switch to it
    target = page
    if popup_holder["page"] is not None:
        target = popup_holder["page"]
        try:
            target.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        try:
            target.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        try:
            target.bring_to_front()
        except Exception:
            pass
        log(f"[AUTO-START] {trader_name}: copy flow opened in popup -> {target.url}")
    elif len(ctx_before.pages) > pages_before:
        target = ctx_before.pages[-1]
        try:
            target.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        log(f"[AUTO-START] {trader_name}: copy flow opened in new context page -> {target.url}")

    url_after = ""
    try:
        url_after = target.url
    except Exception:
        pass
    if url_after and url_after != url_before:
        log(f"[AUTO-START] {trader_name}: URL changed {url_before} -> {url_after}")

    # Save page HTML for offline inspection if no modal/popup detected
    if target is page and url_after == url_before:
        try:
            html = page.content()
            html_path = os.path.join(SCREENSHOT_DIR, f"start_post_click_{trader_name}.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            log(f"[AUTO-START] {trader_name}: no modal/nav detected. Saved HTML: {html_path}")
        except Exception:
            pass

    # Screenshot the ACTUAL target page (not _get_page() which returns the original)
    try:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        post_click_shot = os.path.join(SCREENSHOT_DIR, f"start_post_click_{trader_name}_{ts}.png")
        target.screenshot(path=post_click_shot, full_page=True)
        log(f"[AUTO-START] {trader_name}: post-click snapshot: {post_click_shot}")
    except Exception as e:
        log(f"[AUTO-START] {trader_name}: post-click screenshot failed: {e}")

    # Save the target's HTML too (always — helps tune selectors when input misses)
    try:
        html_path = os.path.join(SCREENSHOT_DIR, f"start_post_click_{trader_name}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(target.content())
    except Exception:
        pass

    # Use `target` (popup if any) for all subsequent locators
    page = target

    # Detect post-click invitation modal → blacklist & close
    try:
        page_text_modal = (page.content() or "").lower()
    except Exception:
        page_text_modal = ""
    if any(m in page_text_modal for m in _PRIVATE_MARKERS):
        _screenshot(f"start_skip_private_modal_{trader_name}")
        _blacklist_trader(trader_name, "invitation-code modal")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return

    amount_str = str(int(amount_usdt))

    # Step 2a — stay on the DEFAULT tab (Fixed Ratio). It only requires the
    # Copy Amount field; Fixed Amount additionally requires Cost Per Order and
    # is more error-prone. Fixed Ratio with $100 means "$100 capital sized
    # proportionally to the lead's positions" — matches the existing 黑皮哥哥
    # copy.

    # Step 2b — fill the "Copy Amount" field. The page renders BOTH tabs' inputs
    # (the inactive tab's are hidden). In Fixed Ratio mode the Copy Amount has
    # placeholder "100-200,000". Pick the VISIBLE one matching the active tab.
    filled = False
    candidate_selectors = [
        'input.bn-textField-input[placeholder="100-200,000"]',
        'input.bn-textField-input[placeholder="10-200,000"]',
        'input.bn-textField-input[placeholder*="200,000"]',
    ]
    for sel in candidate_selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            for i in range(count):
                inp = loc.nth(i)
                try:
                    if not inp.is_visible():
                        continue
                except Exception:
                    continue
                ph = ""
                try:
                    ph = inp.get_attribute("placeholder") or ""
                except Exception:
                    pass
                inp.click()
                try:
                    inp.press("Control+a")
                except Exception:
                    pass
                inp.type(amount_str, delay=50)
                filled = True
                log(f"[AUTO-START] {trader_name}: amount filled via {sel}[{i}] (placeholder='{ph}')")
                break
            if filled:
                break
        except Exception:
            continue
    if not filled:
        log(f"[AUTO-START] {trader_name}: 'Copy Amount' input not found.", alert=True)
        _screenshot(f"start_fail_amount_{trader_name}", page=page)
        beep(5)
        return

    # Step 2c — detect insufficient balance BEFORE the checkbox/Copy step. The
    # page renders "Your balance is too low to perform this transaction" and
    # keeps the Copy button disabled, which would otherwise be misreported as a
    # checkbox failure. Read the available USDT and back off with a cooldown.
    try:
        page_text_bal = (page.content() or "")
    except Exception:
        page_text_bal = ""
    low_markers = ("balance is too low", "your balance is too low",
                   "insufficient balance", "insufficient asset")
    if any(m in page_text_bal.lower() for m in low_markers):
        avail = ""
        try:
            m = re.search(r"Available\s*([\d.,]+)\s*USDT", page_text_bal)
            if m:
                avail = m.group(1)
        except Exception:
            pass
        _INSUFFICIENT_FUNDS_UNTIL[trader_name] = time.time() + INSUFFICIENT_FUNDS_COOLDOWN
        _screenshot(f"start_low_balance_{trader_name}", page=page)
        log(f"[AUTO-START] {trader_name}: insufficient free balance to fund "
            f"${amount_str} copy"
            + (f" (only {avail} USDT available)" if avail else "")
            + f". Backing off {INSUFFICIENT_FUNDS_COOLDOWN // 60} min. "
            "Deposit/free up USDT to add this trader.", alert=True)
        try:
            if page.context and len(page.context.pages) > 1:
                page.close()
        except Exception:
            pass
        return

    # Step 3 — tick the agreement checkbox. Verify by polling Copy button enabled state.
    def _copy_button_enabled() -> bool:
        try:
            btn = page.locator('button.bn-button__primary:text-is("Copy")').first
            if btn.count() == 0:
                btn = page.locator('button:text-is("Copy")').first
            if btn.count() == 0:
                return False
            if not btn.is_visible():
                return False
            if btn.is_disabled():
                return False
            aria_dis = (btn.get_attribute("aria-disabled") or "").lower()
            if aria_dis == "true":
                return False
            return True
        except Exception:
            return False

    cb_strategies = [
        ('text-then-checkbox-class',
         lambda: page.locator('text=/I have read and I agreed/').first
                     .locator('xpath=ancestor::*[1]//*[contains(@class,"checkbox") or contains(@class,"bn-checkbox")][1]')
                     .first.click(timeout=1500, force=True)),
        ('text-preceding-sibling',
         lambda: page.locator('text=/I have read and I agreed/').first
                     .locator('xpath=preceding-sibling::*[1]')
                     .first.click(timeout=1500, force=True)),
        ('role-checkbox',
         lambda: page.get_by_role("checkbox").first.click(timeout=1500, force=True)),
        ('checkbox-input',
         lambda: page.locator('input[type="checkbox"]').first.click(timeout=1500, force=True)),
        ('label-click',
         lambda: page.locator('text=/I have read and I agreed/').first.click(timeout=1500, force=True)),
    ]
    cb_ok = False
    for label, fn in cb_strategies:
        try:
            fn()
        except Exception:
            continue
        time.sleep(0.7)
        if _copy_button_enabled():
            log(f"[AUTO-START] {trader_name}: agreement checkbox toggled via {label}")
            cb_ok = True
            break
    if not cb_ok:
        _screenshot(f"start_fail_checkbox_{trader_name}", page=page)
        log(f"[AUTO-START] {trader_name}: could not enable Copy button (checkbox toggle failed).", alert=True)
        beep(5)
        return

    # Step 4 — confirm (or skip in dry-run)
    if DRY_RUN:
        path = _screenshot(f"start_dryrun_{trader_name}", page=page)
        log(f"[AUTO-START] {trader_name}: DRY_RUN — form filled with ${amount_str}, NOT clicking final Copy. Screenshot: {path}", alert=True)
        beep(2)
        # Close the popup tab (if it's a popup) without submitting
        try:
            if page.context and len(page.context.pages) > 1:
                page.close()
        except Exception:
            pass
        return

    # Live mode — two-step: outer "Copy" opens confirmation modal, then "Confirm" fires the API.
    outer_copy_selectors = [
        'button.bn-button__primary:text-is("Copy")',
        'button[class*="bn-button__primary"]:text-is("Copy")',
        'button:text-is("Copy"):not([aria-disabled="true"])',
    ]
    if not _click_first_visible(page, outer_copy_selectors, timeout_each_ms=4000):
        log(f"[AUTO-START] {trader_name}: outer Copy button not found.", alert=True)
        _screenshot(f"start_fail_outer_{trader_name}", page=page)
        beep(5)
        return

    # Wait for the Confirmation modal to appear
    try:
        page.wait_for_selector('text=/^Confirmation$/', timeout=8_000)
    except Exception:
        log(f"[AUTO-START] {trader_name}: Confirmation modal did not appear.", alert=True)
        _screenshot(f"start_no_modal_{trader_name}", page=page)
        beep(5)
        return
    _screenshot(f"start_modal_{trader_name}", page=page)

    confirm_selectors = [
        'button.bn-button__primary:text-is("Confirm")',
        'button[class*="bn-button__primary"]:text-is("Confirm")',
        'button:text-is("Confirm")',
    ]
    copy_id = ""
    api_msg = ""
    submit_ok = False
    try:
        with page.expect_response(
            lambda r: "copy-portfolio" in r.url and r.request.method == "POST",
            timeout=20_000,
        ) as resp_info:
            if not _click_first_visible(page, confirm_selectors, timeout_each_ms=4000):
                log(f"[AUTO-START] {trader_name}: Confirm button not found in modal.", alert=True)
                _screenshot(f"start_fail_confirm_{trader_name}", page=page)
                beep(5)
                return
        resp = resp_info.value
        try:
            data = resp.json() or {}
            api_msg = str(data.get("message") or data.get("msg") or "")
            code_val = data.get("code")
            success_flag = data.get("success")
            payload = data.get("data") or {}
            if isinstance(payload, dict):
                copy_id = str(
                    payload.get("copyPortfolioId")
                    or payload.get("portfolioId")
                    or payload.get("id")
                    or ""
                )
            if success_flag is True or (code_val in (None, "000000", 0, "0") and not api_msg.lower().startswith("fail")):
                submit_ok = True
        except Exception:
            submit_ok = resp.ok
    except Exception as e:
        log(f"[AUTO-START] {trader_name}: submit response not captured: {e}", alert=True)
        _screenshot(f"start_fail_response_{trader_name}", page=page)
        beep(5)
        return

    time.sleep(2)
    shot = _screenshot(f"start_after_{trader_name}", page=page)

    if not submit_ok:
        log(f"[AUTO-START] {trader_name}: API rejected ({api_msg or 'no message'}). Screenshot: {shot}", alert=True)
        beep(5)
        return

    log(f"[AUTO-START] {trader_name}: copy started ({amount_usdt} USDT) copy_id={copy_id or '(unknown)'}. Screenshot: {shot}", alert=True)
    ACTIVE_COPIES[trader_name] = amount_usdt
    _persist_active_copies()
    TRADER_IDS[trader_name] = {"lead_id": lead_portfolio_id, "copy_id": copy_id}
    _persist_trader_ids()
    beep(3)


def execute_stop(trader_name: str) -> None:
    """Stop copying a trader via the real copy-management page.

    Binance moved the old /my/copy-trading/spot/portfolios URL (now a 404).
    The live page is /copy-trading/copy-management?biz=spot, where each ongoing
    copy is a card with Adjust Balance / Pause / Settings + an icon-only power
    (Stop) button. Clicking it opens a modal whose primary button closes the
    copy (POST .../copy-portfolio/close).
    """
    if not _ensure_logged_in():
        log(f"[AUTO-STOP] {trader_name}: not authenticated — skipping.", alert=True)
        return

    page = _get_page()
    mgmt_url = "https://www.binance.com/en/copy-trading/copy-management?biz=spot"
    log(f"[AUTO-STOP] {trader_name}: opening {mgmt_url}")
    try:
        page.goto(mgmt_url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        log(f"[AUTO-STOP] {trader_name}: navigation failed: {e}", alert=True)
        return
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass

    # Let the ongoing-copies list render and lazy-load.
    for _ in range(4):
        try:
            page.mouse.wheel(0, 1200)
        except Exception:
            pass
        time.sleep(0.8)
    try:
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    time.sleep(0.8)

    try:
        page_text = page.content() or ""
    except Exception:
        page_text = ""

    # If the trader is no longer in the ongoing list, the copy is already
    # closed — clear it so the exit logic stops re-firing every poll.
    if trader_name not in page_text:
        log(f"[AUTO-STOP] {trader_name}: not in ongoing copies — already closed. "
            f"Clearing from ACTIVE_COPIES.", alert=True)
        _screenshot(f"stop_already_closed_{trader_name}")
        ACTIVE_COPIES.pop(trader_name, None)
        _entry_snapshots.pop(trader_name, None)
        _persist_active_copies()
        beep(2)
        return

    # Isolate THIS trader's card: nearest ancestor div that owns a Settings
    # button. This avoids grabbing a parent that spans multiple cards.
    try:
        name_el = page.get_by_text(trader_name, exact=False).first
        name_el.wait_for(state="visible", timeout=6000)
        card = name_el.locator(
            "xpath=ancestor::div[.//button[contains(normalize-space(.),'Settings')]][1]"
        ).first
        card.wait_for(state="visible", timeout=4000)
    except Exception:
        log(f"[AUTO-STOP] {trader_name}: card not isolated on management page. "
            f"MANUAL ACTION NEEDED — stop this copy on Binance by hand.", alert=True)
        _screenshot(f"stop_fail_row_{trader_name}")
        beep(5)
        return

    # The Stop control is the icon-only (empty-text) button in this card.
    stop_btn = None
    try:
        cbtns = card.locator("button")
        for i in range(cbtns.count()):
            b = cbtns.nth(i)
            try:
                if b.is_visible() and (b.inner_text() or "").strip() == "":
                    stop_btn = b  # last empty-text visible button = power icon
            except Exception:
                continue
    except Exception:
        pass

    if stop_btn is None:
        log(f"[AUTO-STOP] {trader_name}: Stop (power) icon not found in card.", alert=True)
        _screenshot(f"stop_fail_btn_{trader_name}")
        beep(5)
        return

    try:
        stop_btn.scroll_into_view_if_needed()
        stop_btn.click()
    except Exception as e:
        log(f"[AUTO-STOP] {trader_name}: could not click Stop icon: {e}", alert=True)
        _screenshot(f"stop_fail_btn_{trader_name}")
        beep(5)
        return

    # Confirmation modal
    time.sleep(1.5)
    if DRY_RUN:
        shot = _screenshot(f"stop_dryrun_{trader_name}")
        log(f"[AUTO-STOP] {trader_name}: DRY_RUN — stop modal opened, NOT confirming. Screenshot: {shot}", alert=True)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        beep(2)
        return

    # Click the modal's primary button and verify via the close API response.
    confirm_selectors = [
        "div[role='dialog'] button.bn-button__primary",
        "button.bn-button__primary:has-text('Stop')",
        "button.bn-button__primary:has-text('Confirm')",
        "button:has-text('Stop')",
        "button:has-text('Confirm')",
    ]
    api_ok = False
    api_msg = ""
    try:
        with page.expect_response(
            lambda r: ("copy-portfolio/close" in r.url or
                       ("copy" in r.url and r.request.method == "POST" and
                        any(k in r.url.lower() for k in ("close", "stop", "end", "cancel")))),
            timeout=20_000,
        ) as resp_info:
            if not _click_first_visible(page, confirm_selectors, timeout_each_ms=4000):
                log(f"[AUTO-STOP] {trader_name}: confirm button not found in stop modal.", alert=True)
                _screenshot(f"stop_fail_confirm_{trader_name}")
                beep(5)
                return
        resp = resp_info.value
        try:
            data = resp.json() or {}
            api_msg = str(data.get("message") or data.get("msg") or "")
            code = data.get("code")
            if data.get("success") is True or code in (None, "000000", 0, "0"):
                api_ok = True
        except Exception:
            api_ok = resp.ok
    except Exception as e:
        log(f"[AUTO-STOP] {trader_name}: stop response not captured: {e}", alert=True)
        _screenshot(f"stop_fail_response_{trader_name}")
        beep(5)
        return

    time.sleep(3)
    shot = _screenshot(f"stop_after_{trader_name}")
    if not api_ok:
        log(f"[AUTO-STOP] {trader_name}: stop API rejected ({api_msg or 'no message'}). Screenshot: {shot}", alert=True)
        beep(5)
        return

    log(f"[AUTO-STOP] {trader_name}: stop confirmed (copy closed). Screenshot: {shot}", alert=True)
    ACTIVE_COPIES.pop(trader_name, None)
    _entry_snapshots.pop(trader_name, None)
    _persist_active_copies()
    beep(3)


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
    return


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
        executable_path=r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        args=[
            "--no-first-run",
            "--disable-popup-blocking",
            "--disable-notifications",
            "--disable-blink-features=AutomationControlled",
        ],
    )

    # Inject saved cookies as fallback auth
    import json as _json
    if os.path.exists(COOKIE_FILE):
        try:
            _cookies = _json.loads(open(COOKIE_FILE, encoding="utf-8").read())
            _browser.add_cookies(_cookies)
            log(f"Injected {len(_cookies)} saved cookies into session.")
        except Exception as _ce:
            log(f"Cookie injection skipped: {_ce}")

    if _browser.pages:
        _page = _browser.pages[0]
    else:
        _page = _browser.new_page()

    url = _page.url or ""
    if "binance.com" not in url:
        log("Opening Binance copy trading page…")
        try:
            _page.goto("https://www.binance.com/en/copy-trading/spot",
                       wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass

    # Final wait for the page's network to settle before we start fetching
    try:
        _page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass  # networkidle timeout is acceptable

    log(f"Page ready at: {_page.url[:80]}")
    return _page


def _reset_browser() -> None:
    """Close the browser so the next poll restarts it cleanly.

    Guarded: refuses to close while the user is mid-login on accounts.binance.com,
    which would otherwise kick them back to step one of the login flow.
    """
    global _browser, _page
    try:
        if _page is not None:
            try:
                if "accounts.binance.com" in (_page.url or ""):
                    log("[reset] Skipped browser reset — user is mid-login.")
                    return
            except Exception:
                pass
        if _browser:
            _browser.close()
    except Exception:
        pass
    _browser = None
    _page    = None


def _save_cookies() -> None:
    """Persist current browser cookies to disk so a future reset can restore session."""
    global _browser
    try:
        if _browser is None:
            return
        import json as _j
        cookies = _browser.cookies()
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            _j.dump(cookies, f)
        log(f"[cookies] Saved {len(cookies)} cookies to disk.")
    except Exception as e:
        log(f"[cookies] Save failed: {e}")


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


def _fetch_7d_roi_map() -> dict:
    """Fetch the 7D leaderboard and return {pid: roi_7d} map."""
    page = _get_page()
    roi_map: dict = {}
    for page_num in range(1, 10):
        payload = {**BASE_PAYLOAD, "pageNumber": page_num, "timeRange": "7D"}
        try:
            data = page.evaluate(_FETCH_JS, {"endpoint": ENDPOINT, "payload": payload})
        except Exception:
            break
        lst = (data or {}).get("data", {}).get("list") or []
        if not lst:
            break
        for t in lst:
            pid = str(t.get("leadPortfolioId") or "")
            if pid:
                roi_map[pid] = float(t.get("roi") or 0)
        total = int((data or {}).get("data", {}).get("total") or 0)
        if total and len(roi_map) >= total:
            break
    return roi_map


def fetch_traders() -> list:
    page        = _get_page()
    all_traders = []

    for page_num in range(1, 10):          # up to ~450 traders (9 pages × 50)
        payload = {**BASE_PAYLOAD, "pageNumber": page_num}
        data = None
        for attempt in range(3):
            try:
                data = page.evaluate(_FETCH_JS, {"endpoint": ENDPOINT, "payload": payload})
                break
            except Exception as e:
                log(f"[Fetch] page {page_num} attempt {attempt+1} failed: {str(e)[:80]}")
                if attempt < 2:
                    time.sleep(2)
                    try:
                        page = _get_page()  # soft re-fetch, no browser close
                    except Exception:
                        pass
                else:
                    log(f"[Fetch] page {page_num}: gave up after 3 attempts.")
                    data = None
        if data is None:
            break

        lst = (data or {}).get("data", {}).get("list") or []
        if not lst:
            break

        all_traders.extend(lst)

        total = int((data or {}).get("data", {}).get("total") or 0)
        if total and len(all_traders) >= total:
            break

    # Merge 7D ROI into each trader record
    try:
        roi_7d_map = _fetch_7d_roi_map()
        for t in all_traders:
            pid = str(t.get("leadPortfolioId") or "")
            if pid in roi_7d_map:
                t["roi7D"] = roi_7d_map[pid]
    except Exception as e:
        log(f"[Fetch] 7D ROI merge failed: {e}")

    matched = sum(1 for t in all_traders if "roi7D" in t)
    log(f"Fetched {len(all_traders)} traders "
        f"({matched} with official 7D ROI; rest use momentum slope).")
    return all_traders


# ---------------------------------------------------------------------------
# Trader normalisation
# ---------------------------------------------------------------------------

def _momentum_7d(chart_items):
    """Recent-form signal from the 30D cumulative-ROI curve embedded in every
    leaderboard row (chartItems = ~30 daily cumulative-ROI points).

    Returns the change in cumulative ROI over the last ~7 calendar days, i.e.
    how much the trader gained/lost this week measured on the 30D basis.
    This is NOT Binance's official 7D ROI (different capital basis) — it is a
    consistent, always-available momentum/direction signal for ranking. None
    when the curve is missing/too short.
    """
    if not isinstance(chart_items, list) or len(chart_items) < 2:
        return None
    pts = [c for c in chart_items
           if isinstance(c, dict) and c.get("value") is not None]
    if len(pts) < 2:
        return None
    last = pts[-1]
    today_v = float(last["value"])
    last_ts = last.get("dateTime")
    if last_ts:
        target = last_ts - 7 * 86400000  # 7 days in ms
        prev = min(pts, key=lambda c: abs((c.get("dateTime") or 0) - target))
    else:
        prev = pts[max(0, len(pts) - 8)]
    return today_v - float(prev["value"])


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
        "roi_7":       float(t.get("roi7D") or t.get("roi_7d") or t.get("sevenDayRoi") or -999),
        "mom_7":       _momentum_7d(t.get("chartItems")),
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
    # Core quality (30D)
    if p["mdd"]    <= 5.0:          s += 40   # very low drawdown = best signal
    elif p["mdd"]  <= MAX_MDD:      s += 25
    if p["roi_30"] >= 20.0:         s += 30
    elif p["roi_30"] >= MIN_ROI_30D: s += 15
    if p["days"]   >= 90:           s += 15
    elif p["days"] >= MIN_DAYS:     s += 8
    if p["sharpe"] >= 1.5:          s += 10
    elif p["sharpe"] >= MIN_SHARPE: s += 5
    if p["win_rate"] >= 70.0:       s += 10
    elif p["win_rate"] >= MIN_WIN_RATE: s += 5
    if p["pnl_30"] >= 0:            s += 5
    # Calmar-style risk-adjusted bonus: reward high ROI relative to drawdown.
    calmar = p["roi_30"] / max(p["mdd"], 1.0)
    if calmar   >= 5.0: s += 15   # strong return for very little drawdown
    elif calmar >= 3.0: s += 8
    elif calmar <  1.0: s -= 10   # ROI barely covers the drawdown taken
    # 7D momentum bonus/penalty
    roi7 = p.get("roi_7", -999)
    if roi7 != -999:
        if roi7 >= 5.0:    s += 10   # strong recent form
        elif roi7 >= 0.0:  s += 5    # positive this week
        elif roi7 < -5.0:  s -= 15   # losing this week — big penalty
        elif roi7 < 0.0:   s -= 5    # slightly negative
    # Recent-form momentum from the cumulative curve (available for everyone,
    # even traders absent from the official 7D top-list). Confirms/denies that
    # the 30D edge is still intact this week.
    mom = p.get("mom_7")
    if mom is not None:
        if mom   >= 10.0: s += 10   # accelerating hard over the last week
        elif mom >=  2.0: s += 5    # rising
        elif mom <= -10.0: s -= 12  # falling sharply — momentum rolling over
        elif mom <   0.0: s -= 5    # drifting down
    # Penalties
    if p["roi_30"] > MAX_ROI_30D:   s -= 25   # martingale / extreme leverage
    if p["mdd"]    > MAX_MDD:       s -= 30
    if p["sharpe"] < 0:             s -= 10
    if p["slots_free"] < MIN_SLOTS: s -= 20   # full — can't enter
    return max(0, min(100, s))


def _is_already_copying(name: str) -> bool:
    return any(name.lower() in k.lower() or k.lower() in name.lower()
               for k in ACTIVE_COPIES)


def _passes_momentum(p: dict) -> bool:
    """Recent-form entry gate combining official 7D ROI and the momentum slope.

    Priority 1: official 7D ROI (exact) — if known, the trader must not be
                losing this week (>= MIN_ROI_7D).
    Priority 2: when the official 7D ROI is unavailable (trader not in the 7D
                top-list), fall back to the cumulative-curve momentum slope:
                the last-7-day trend must be rising (>= 0).
    If neither signal exists, honour REQUIRE_ROI_7D (block when required).
    """
    roi7 = p.get("roi_7", -999)
    if roi7 != -999:
        return roi7 >= MIN_ROI_7D
    mom = p.get("mom_7")
    if mom is not None:
        return mom >= 0.0
    return not REQUIRE_ROI_7D


def _roi7_str(p: dict) -> str:
    r = p.get("roi_7", -999)
    return f"{r:5.1f}%" if r != -999 else "  n/a "


def _mom7_str(p: dict) -> str:
    m = p.get("mom_7")
    return f"{m:+5.1f}" if m is not None else "  n/a"


def _total_deployed() -> float:
    return sum(ACTIVE_COPIES.values())


# ---------------------------------------------------------------------------
# Check active copies + auto-discovery
# ---------------------------------------------------------------------------

def check(traders: list) -> None:
    parsed  = [parse_trader(t) for t in traders]
    by_name = {p["name"]: p for p in parsed}

    # Read YOUR real copier P&L once per poll (actual positions, not estimates).
    real_positions = _fetch_my_copy_positions()

    def _real_for(active_name: str):
        for k, v in real_positions.items():
            if active_name.lower() in k.lower() or k.lower() in active_name.lower():
                return v
        return None

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
        roi_entry = snap.get("roi_at_entry", match["roi_30"])

        # Trailing floor: if in profit (roi > entry), tighten the drop exit
        in_profit = match["roi_30"] > roi_entry
        effective_exit_roi_drop = EXIT_ROI_DROP / 2 if in_profit else EXIT_ROI_DROP  # 5% trailing if in profit

        # Determine exit reason
        exit_reason = None
        if match["mdd"] > EXIT_MDD:
            exit_reason = f"MDD {match['mdd']:.1f}% > hard limit {EXIT_MDD}%"
        elif match["roi_30"] < EXIT_ROI_FLOOR:
            exit_reason = f"30D ROI {match['roi_30']:.1f}% < floor {EXIT_ROI_FLOOR}%"
        elif (match["mdd"] - mdd_entry) > EXIT_MDD_SPIKE:
            exit_reason = f"MDD spiked +{match['mdd']-mdd_entry:.1f}% since entry (was {mdd_entry:.1f}% → now {match['mdd']:.1f}%)"
        elif (roi_peak - match["roi_30"]) > effective_exit_roi_drop:
            floor_label = f"trailing {effective_exit_roi_drop:.0f}%" if in_profit else f"{effective_exit_roi_drop:.0f}%"
            exit_reason = f"ROI dropped {roi_peak-match['roi_30']:.1f}% from peak {roi_peak:.1f}% → {match['roi_30']:.1f}% (floor: {floor_label})"

        if exit_reason:
            # Key by reason TYPE (first word) so a repeatedly-failing stop
            # does not spam a new alert every poll as the ROI number changes.
            reason_type = exit_reason.split()[0]
            key = f"exit_{match['name']}_{reason_type}"
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
            real = _real_for(active_name)
            real_str = ""
            if real and real.get("pnl") is not None:
                real_str = f"  | REAL PnL ${real['pnl']:+.2f}"
                if real.get("roi") is not None:
                    real_str += f" ({real['roi']:+.1f}%)"
            log(
                f"[HOLDING] {active_name}: ROI {match['roi_30']:.1f}%  MDD {match['mdd']:.1f}%  "
                f"Sharpe {match['sharpe']:.2f}  WinRate {match['win_rate']:.0f}%  "
                f"~${proj_daily:+.2f}/day  ~${proj_weekly:+.2f}/wk  Score:{score(match)}{real_str}"
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

    # ── 2b. REAL copier P&L (actual Binance positions, not estimates) ───────
    if real_positions:
        total_real_pnl = sum(
            v["pnl"] for v in real_positions.values() if v.get("pnl") is not None
        )
        total_real_principal = sum(
            v["principal"] for v in real_positions.values() if v.get("principal") is not None
        )
        _persist_real_pnl(real_positions, total_real_pnl, total_real_principal)
        names = ", ".join(sorted(real_positions.keys()))
        principal_str = (
            f" on ${total_real_principal:.0f} principal" if total_real_principal else ""
        )
        log(
            f"[REAL-PNL] Actual copy P&L across {len(real_positions)} position(s): "
            f"${total_real_pnl:+.2f}{principal_str}  ({names})"
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
                    and _passes_momentum(p)
                    and p["days"]  >= MIN_DAYS
                    and p["sharpe"] >= MIN_SHARPE
                    and p["slots_free"] >= MIN_SLOTS
                    and (p["win_rate"] == 0 or p["win_rate"] >= MIN_WIN_RATE)
                    and score(p)   >= MIN_SCORE
                    and not _is_already_copying(p["name"])
                    and p["name"] not in BLACKLIST
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
                f"  ROI30:{p['roi_30']:.1f}%  ROI7:{_roi7_str(p)}  Mom7:{_mom7_str(p)}  MDD:{p['mdd']:.1f}%  Days:{p['days']}  "
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
                log(f"    {p['name']:25} ROI30:{p['roi_30']:6.1f}%  ROI7:{_roi7_str(p)}  Mom7:{_mom7_str(p)}  MDD:{p['mdd']:5.1f}%  "
                    f"Days:{p['days']:4}  Score:{score(p):3}  Slots:{p['slots_free']}")
    else:
        reason = f"slots full ({len(ACTIVE_COPIES)}/{MAX_COPIES})" if slots_available <= 0 \
                 else f"budget exhausted (${_total_deployed():.0f}/${MAX_DEPLOYED:.0f})"
        log(f"  Auto-enter paused: {reason}")

    # ── 4. Auto-rotation: upgrade an idle, low-scoring holding ──────────────
    # The only no-deposit way to improve the portfolio when free balance is too
    # low to add a slot: stop an idle weak copy (frees its USDT) and start a much
    # stronger one. Conservative — never touches a copy with real profit/loss.
    if ENABLE_ROTATION and ACTIVE_COPIES:
        rot_candidates = sorted(
            (p for p in parsed
             if p["mdd"] <= MAX_MDD
             and MIN_ROI_30D <= p["roi_30"] <= MAX_ROI_30D
             and _passes_momentum(p)
             and p["days"] >= MIN_DAYS
             and p["sharpe"] >= MIN_SHARPE
             and p["slots_free"] >= MIN_SLOTS
             and (p["win_rate"] == 0 or p["win_rate"] >= MIN_WIN_RATE)
             and score(p) >= MIN_SCORE
             and not _is_already_copying(p["name"])
             and p["name"] not in BLACKLIST),
            key=score, reverse=True,
        )
        best = rot_candidates[0] if rot_candidates else None
        if best is not None:
            best_sc = score(best)
            # Pick the weakest IDLE held trader eligible to be replaced.
            weakest = None
            weakest_sc = None
            for active_name in ACTIVE_COPIES:
                held = next((p for n, p in by_name.items()
                             if active_name.lower() in n.lower()), None)
                if held is None:
                    continue
                rp = _real_for(active_name)
                rp_val = rp.get("pnl") if rp else None
                # Only rotate idle/flat positions — leave winners and real losers
                # to ride / be handled by the risk-based exit logic.
                if rp_val is not None and abs(rp_val) > ROTATION_FLAT_BAND:
                    continue
                if time.time() - _LAST_ROTATION.get(active_name, 0) < ROTATION_COOLDOWN:
                    continue
                held_sc = score(held)
                if weakest_sc is None or held_sc < weakest_sc:
                    weakest, weakest_sc = active_name, held_sc
            if weakest is not None and best_sc - weakest_sc >= ROTATION_SCORE_MARGIN:
                proj_daily = CAPITAL_PER_TRADER * (best["roi_30"] / 100) / 30
                log(
                    f"[AUTO-ROTATE] Upgrading slot: stopping {weakest} "
                    f"(score {weakest_sc}, idle $0) → starting {best['name']} "
                    f"(score {best_sc}, ROI {best['roi_30']:.1f}%, est ${proj_daily:.2f}/day).",
                    alert=True,
                )
                beep(4)
                _LAST_ROTATION[weakest] = time.time()
                execute_stop(weakest)
                # Only deploy the replacement if the stop actually freed the slot.
                if weakest not in ACTIVE_COPIES:
                    # Funds were just freed — clear any stale low-balance cooldown
                    # so the replacement can fund immediately this poll.
                    _INSUFFICIENT_FUNDS_UNTIL.pop(best["name"], None)
                    execute_start(best["name"], best["pid"], CAPITAL_PER_TRADER)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    global _login_failed_this_poll
    _load_trader_ids()
    _load_active_copies()
    _load_blacklist()
    log("=== Binance Copy Monitor starting ===")
    log(f"  Goal: ${DAILY_TARGET:.2f}/day | Max {MAX_COPIES} traders | Max ${MAX_DEPLOYED:.0f} deployed")
    log(f"  Entry:  MDD<={MAX_MDD}%  ROI {MIN_ROI_30D}-{MAX_ROI_30D}%  Days>={MIN_DAYS}  Score>={MIN_SCORE}")
    log(f"  Exit:   MDD>{EXIT_MDD}%  ROI<{EXIT_ROI_FLOOR}%  MDD spike>{EXIT_MDD_SPIKE}%  ROI drop>{EXIT_ROI_DROP}%")
    if BLACKLIST:
        log(f"  Blacklist: {len(BLACKLIST)} trader(s) skipped")
    if ACTIVE_COPIES:
        log(f"  Active: { {k: f'${v:.0f}' for k,v in ACTIVE_COPIES.items()} }")

    poll_count = 0
    while True:
        poll_count += 1
        log(f"--- Poll #{poll_count} ---")
        _login_failed_this_poll = False
        try:
            # Auth gate: if not logged in, prompt once and skip the rest of
            # this poll. Avoids hammering the API (and the browser) while the
            # user is still typing their password / handling 2FA.
            if not _ensure_logged_in():
                log("Auth not ready this poll — skipping fetch. Will retry next cycle.")
                _record_auth_failure()
            else:
                _clear_auth_failure()
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
