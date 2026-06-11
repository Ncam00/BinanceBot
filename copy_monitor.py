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
CAPITAL_PER_TRADER = 125.0   # base USDT per trader
MAX_COPIES         = 4        # never hold more than this many at once
MAX_DEPLOYED       = 500.0    # hard cap on total USDT deployed across all copies
DAILY_TARGET       = 2.14     # $15/wk goal ($2.14/day)

# ---- Entry thresholds (auto-start when ALL met) ----------------------------
MIN_DAYS      = 30     # ≥30 days history (relaxed to find more candidates)
MAX_MDD       = 15.0   # max drawdown %
MIN_ROI_30D   = 15.0   # minimum 30-day ROI % (quality gate)
MIN_ROI_7D    = 0.0    # minimum 7-day ROI % (momentum gate — must not be losing this week)
MAX_ROI_30D   = 150.0  # cap — filters obvious martingale blow-ups
MIN_WIN_RATE  = 55.0   # minimum win rate % (if available)
MIN_SHARPE    = 1.0    # minimum Sharpe ratio
MIN_SLOTS     = 1      # must have at least 1 free slot
MIN_SCORE     = 65     # minimum composite score (0-100)

# ---- Exit thresholds (auto-stop when ANY hit) ------------------------------
EXIT_MDD           = 20.0   # hard exit: MDD crosses this
EXIT_ROI_FLOOR     = -5.0   # hard exit: 30D ROI goes negative beyond this
EXIT_MDD_SPIKE     = 7.0    # exit if MDD rises by this much since we started copying
EXIT_ROI_DROP      = 10.0   # exit if ROI drops by this much since peak

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

MIN_DAYS      = 30     # kept for compatibility (now in config block above)
MAX_MDD       = 15.0
MIN_ROI_30D   = 8.0
MAX_ROI_30D   = 150.0

# Exit thresholds for traders you are actively copying
EXIT_MDD      = 20.0
EXIT_ROI_FLOOR = -5.0

POLL_INTERVAL = 300    # seconds between polls (5 min)
LOG_FILE      = r"C:\BinanceBot\copy_alerts.log"
DATA_DIR      = r"C:\BinanceBot\playwright_session"   # logged-in Chrome profile
COOKIE_FILE   = r"C:\BinanceBot\session_cookies.json" # backup cookies
TRADER_IDS_FILE = r"C:\BinanceBot\trader_ids.json"     # persisted copy_ids
SCREENSHOT_DIR = r"C:\BinanceBot\screenshots"          # UI-action proof shots
BLACKLIST_FILE = r"C:\BinanceBot\blacklist.json"        # traders we cannot copy (private/error)

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
    const r = await fetch(args.url, {
        method: 'POST',
        credentials: 'include',
        headers: {
            'content-type': 'application/json',
            'clienttype': 'web',
            'x-source': 'web',
            'bnc-location': 'BINANCE',
            'lang': 'en',
        },
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
    TRADER_IDS[trader_name] = {"lead_id": lead_portfolio_id, "copy_id": copy_id}
    _persist_trader_ids()
    beep(3)


def execute_stop(trader_name: str) -> None:
    """Stop copying a trader by driving the Binance UI."""
    if not _ensure_logged_in():
        log(f"[AUTO-STOP] {trader_name}: not authenticated — skipping.", alert=True)
        return

    page = _get_page()
    portfolios_url = "https://www.binance.com/en/my/copy-trading/spot/portfolios"
    log(f"[AUTO-STOP] {trader_name}: opening {portfolios_url}")
    try:
        page.goto(portfolios_url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        log(f"[AUTO-STOP] {trader_name}: navigation failed: {e}", alert=True)
        return
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass

    # Find the row that contains the trader name, then click Stop within it
    try:
        row = page.locator(f"text={trader_name}").first
        row.wait_for(state="visible", timeout=8000)
    except Exception:
        log(f"[AUTO-STOP] {trader_name}: trader row not found on portfolios page.", alert=True)
        _screenshot(f"stop_fail_row_{trader_name}")
        beep(5)
        return

    # Walk up to the row container then click stop within it
    clicked = False
    try:
        container = row.locator("xpath=ancestor::*[self::tr or self::div][1]")
        for sel in _STOP_BUTTON_SELECTORS:
            btn = container.locator(sel).first
            try:
                btn.wait_for(state="visible", timeout=2000)
                btn.click()
                clicked = True
                break
            except Exception:
                continue
    except Exception:
        pass

    if not clicked:
        # Fallback: click any visible Stop button on the page
        clicked = _click_first_visible(page, _STOP_BUTTON_SELECTORS, timeout_each_ms=4000)

    if not clicked:
        log(f"[AUTO-STOP] {trader_name}: 'Stop' button not found.", alert=True)
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

    confirm_clicked = _click_first_visible(page, _CONFIRM_BUTTON_SELECTORS, timeout_each_ms=4000)
    if not confirm_clicked:
        log(f"[AUTO-STOP] {trader_name}: confirm button not found in stop modal.", alert=True)
        _screenshot(f"stop_fail_confirm_{trader_name}")
        beep(5)
        return

    time.sleep(3)
    shot = _screenshot(f"stop_after_{trader_name}")
    log(f"[AUTO-STOP] {trader_name}: stop submitted. Screenshot: {shot}", alert=True)
    ACTIVE_COPIES.pop(trader_name, None)
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

    log(f"Fetched {len(all_traders)} traders (with 7D ROI).")
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
        "roi_7":       float(t.get("roi7D") or t.get("roi_7d") or t.get("sevenDayRoi") or -999),
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
    if p["sharpe"] >= 1.0:          s += 10
    elif p["sharpe"] >= MIN_SHARPE: s += 5
    if p["win_rate"] >= 70.0:       s += 10
    elif p["win_rate"] >= MIN_WIN_RATE: s += 5
    if p["pnl_30"] >= 0:            s += 5
    # 7D momentum bonus/penalty
    roi7 = p.get("roi_7", -999)
    if roi7 != -999:
        if roi7 >= 5.0:    s += 10   # strong recent form
        elif roi7 >= 0.0:  s += 5    # positive this week
        elif roi7 < -5.0:  s -= 15   # losing this week — big penalty
        elif roi7 < 0.0:   s -= 5    # slightly negative
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
                    and p.get("roi_7", 0) >= MIN_ROI_7D
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
                f"  ROI30:{p['roi_30']:.1f}%  ROI7:{p.get('roi_7', float('nan')):.1f}%  MDD:{p['mdd']:.1f}%  Days:{p['days']}  "
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
                log(f"    {p['name']:25} ROI30:{p['roi_30']:6.1f}%  ROI7:{p.get('roi_7', float('nan')):6.1f}%  MDD:{p['mdd']:5.1f}%  "
                    f"Days:{p['days']:4}  Score:{score(p):3}  Slots:{p['slots_free']}")
    else:
        reason = f"slots full ({len(ACTIVE_COPIES)}/{MAX_COPIES})" if slots_available <= 0 \
                 else f"budget exhausted (${_total_deployed():.0f}/${MAX_DEPLOYED:.0f})"
        log(f"  Auto-enter paused: {reason}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    global _login_failed_this_poll
    _load_trader_ids()
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
