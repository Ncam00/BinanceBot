# -*- coding: utf-8 -*-
"""
auto_copy.py — Fully automated copy trade + BAPI endpoint capture.
- Navigates to trader page
- Accepts OneTrust consent
- Clicks Copy, fills 100 USDT, confirms
- Captures all BAPI network calls throughout
- Saves captured endpoints to bapi_endpoints.json
"""
import json
import time
import sys
from playwright.sync_api import sync_playwright

CHROME_EXE = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
SESSION_DIR = r"C:\BinanceBot\playwright_session"
OUTPUT_FILE = r"C:\BinanceBot\bapi_endpoints.json"
AMOUNT = "100"

# 华子弟 — 63% ROI, 1.62% MDD, Spot, API-connected
TRADER_NAME = "华子弟"
TRADER_URL = "https://www.binance.com/en/copy-trading/lead-details/5010515263519011585?timeRange=30D"

INTERESTING_PATTERNS = [
    "copy-trade", "copyTrade", "copy_trade",
    "follower", "follow", "portfolio",
    "spot-copy", "future-copy",
    "lead-details",
]

def is_interesting(url):
    url_lower = url.lower()
    return any(p.lower() in url_lower for p in INTERESTING_PATTERNS)

captured = []
request_bodies = {}

def on_request(req):
    if is_interesting(req.url):
        try:
            body = req.post_data
        except Exception:
            body = None
        request_bodies[req.url] = {"method": req.method, "body": body, "headers": dict(req.headers)}
        print(f"  → {req.method} {req.url[:120]}")
        if body:
            print(f"       BODY: {body[:400]}")

def on_response(resp):
    if is_interesting(resp.url):
        try:
            body = resp.json()
        except Exception:
            try:
                body = resp.text()[:500]
            except Exception:
                body = None
        req_info = request_bodies.get(resp.url, {})
        entry = {
            "method": resp.request.method,
            "url": resp.url,
            "status": resp.status,
            "request_body": req_info.get("body"),
            "response_body": body,
        }
        captured.append(entry)
        snippet = json.dumps(body)[:300] if body else ""
        print(f"  ← {resp.status} {resp.url[:120]}")
        if snippet:
            print(f"       RESP: {snippet}")

def accept_onetrust(page):
    """Accept OneTrust cookie consent if present."""
    selectors = [
        "#onetrust-accept-btn-handler",
        ".onetrust-accept-btn-handler",
        "#accept-recommended-btn-handler",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible(timeout=2000):
                btn.first.click()
                print(f"  ✅ OneTrust accepted ({sel})")
                page.wait_for_timeout(1500)
                return True
        except Exception:
            pass
    # Check for Privacy Preference Center modal
    try:
        modal = page.locator('[aria-label="Privacy Preference Center"]')
        if modal.count() > 0 and modal.first.is_visible(timeout=2000):
            for sel in ["#accept-recommended-btn-handler", ".save-preference-btn-handler.onetrust-close-btn-handler"]:
                try:
                    btn = page.locator(sel)
                    if btn.count() > 0:
                        btn.first.click()
                        print(f"  ✅ Privacy modal accepted ({sel})")
                        page.wait_for_timeout(1500)
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False

def find_copy_button(page):
    """Find the exact 'Copy' button (not 'Mock Copy')."""
    try:
        buttons = page.locator("button").all()
        for btn in buttons:
            try:
                txt = btn.inner_text(timeout=500).strip()
                if txt == "Copy":
                    return btn
            except Exception:
                pass
    except Exception:
        pass
    return None

def do_copy_trade(page):
    print(f"\n{'='*60}")
    print(f"Navigating to {TRADER_NAME}...")
    try:
        page.goto(TRADER_URL, wait_until="commit", timeout=30000)
    except Exception as e:
        print(f"  Navigation warning: {e}")
    page.wait_for_timeout(3000)

    # Accept OneTrust before clicking anything
    accept_onetrust(page)
    page.wait_for_timeout(1000)

    # Find and click Copy button
    print("  Looking for Copy button...")
    copy_btn = None
    for attempt in range(5):
        copy_btn = find_copy_button(page)
        if copy_btn:
            break
        page.wait_for_timeout(1500)
        if attempt == 2:
            # Try scrolling up in case button is off-screen
            page.evaluate("window.scrollTo(0, 0)")

    if not copy_btn:
        # Try a JS approach to find it
        try:
            page.evaluate("""
                () => {
                    const btns = [...document.querySelectorAll('button')];
                    const btn = btns.find(b => b.innerText.trim() === 'Copy');
                    if (btn) btn.click();
                }
            """)
            print("  Clicked Copy via JS fallback")
        except Exception as e:
            print(f"  ❌ Copy button not found: {e}")
            return False
    else:
        try:
            copy_btn.scroll_into_view_if_needed()
            copy_btn.hover()
            page.wait_for_timeout(500)
            copy_btn.click()
            print("  ✅ Clicked Copy button")
        except Exception as e:
            print(f"  Click failed, trying JS: {e}")
            page.evaluate("""
                () => {
                    const btns = [...document.querySelectorAll('button')];
                    const btn = btns.find(b => b.innerText.trim() === 'Copy');
                    if (btn) btn.click();
                }
            """)

    page.wait_for_timeout(2000)

    # Dismiss OneTrust if it appeared after click
    accept_onetrust(page)
    page.wait_for_timeout(1000)

    # Wait for the copy dialog (not Privacy modal)
    print("  Waiting for copy dialog...")
    dialog_found = False
    for sel in [".bn-drawer", ".bn-modal", "[class*='copyModal']", "[class*='copy-modal']", "[role='dialog']"]:
        try:
            loc = page.locator(sel)
            if sel == "[role='dialog']":
                # Make sure it's not the Privacy modal
                count = loc.count()
                for i in range(count):
                    aria = loc.nth(i).get_attribute("aria-label") or ""
                    if "Privacy" not in aria:
                        loc.nth(i).wait_for(state="visible", timeout=5000)
                        dialog_found = True
                        print(f"  ✅ Dialog found: {sel} (index {i})")
                        break
            else:
                loc.first.wait_for(state="visible", timeout=5000)
                dialog_found = True
                print(f"  ✅ Dialog found: {sel}")
                break
        except Exception:
            pass
        if dialog_found:
            break

    if not dialog_found:
        print("  ⚠️  No dialog detected — may have opened anyway, proceeding...")

    page.wait_for_timeout(1000)

    # Fill amount
    print(f"  Filling amount: {AMOUNT} USDT...")
    amount_filled = False
    input_selectors = [
        "input[type='number']",
        "input[placeholder*='USDT']",
        "input[placeholder*='Amount']",
        "input[placeholder*='amount']",
        "input[class*='amount']",
        ".bn-input input",
        "input",
    ]
    for sel in input_selectors:
        try:
            inputs = page.locator(sel).all()
            for inp in inputs:
                if inp.is_visible(timeout=1000) and inp.is_enabled(timeout=1000):
                    inp.triple_click()
                    inp.fill(AMOUNT)
                    val = inp.input_value()
                    if AMOUNT in val or val == AMOUNT:
                        print(f"  ✅ Amount filled ({sel}): {val}")
                        amount_filled = True
                        break
        except Exception:
            pass
        if amount_filled:
            break

    if not amount_filled:
        print("  ⚠️  Could not fill amount input — attempting keyboard approach")
        try:
            page.keyboard.press("Tab")
            page.keyboard.type(AMOUNT)
        except Exception as e:
            print(f"  ❌ Amount fill failed: {e}")

    page.wait_for_timeout(1000)

    # Click Confirm button
    print("  Looking for Confirm button...")
    confirm_clicked = False
    confirm_texts = ["Confirm", "Confirm Copy", "Start Copy", "Copy Now"]
    try:
        buttons = page.locator("button").all()
        for btn in buttons:
            try:
                txt = btn.inner_text(timeout=500).strip()
                if any(ct.lower() in txt.lower() for ct in confirm_texts) and btn.is_visible():
                    btn.click()
                    print(f"  ✅ Clicked '{txt}'")
                    confirm_clicked = True
                    break
            except Exception:
                pass
    except Exception:
        pass

    if not confirm_clicked:
        print("  ❌ Confirm button not found")
        return False

    page.wait_for_timeout(3000)

    # Check for success indicators
    success = False
    for indicator in ["success", "successfully", "You are now copying", "Copying started"]:
        try:
            if page.locator(f"text={indicator}").count() > 0:
                success = True
                break
        except Exception:
            pass

    # Also check if button changed to Stop Copy / Manage
    try:
        buttons = page.locator("button").all()
        for btn in buttons:
            try:
                txt = btn.inner_text(timeout=500).strip()
                if txt in ("Stop Copy", "Manage", "Unfollow"):
                    success = True
                    print(f"  ✅ Button changed to '{txt}' — copy confirmed!")
                    break
            except Exception:
                pass
    except Exception:
        pass

    if success:
        print(f"\n  🎉 {TRADER_NAME}: COPY TRADE CONFIRMED!")
    else:
        print(f"\n  ℹ️  Trade submitted — verify on Binance dashboard")

    return True


with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=SESSION_DIR,
        executable_path=CHROME_EXE,
        headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--no-sandbox",
              "--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 900},
        timeout=30000,
    )

    pages = ctx.pages
    page = pages[-1] if pages else ctx.new_page()

    # Wire up network capture
    page.on("request", on_request)
    page.on("response", on_response)

    # Login check — use BAPI response to detect expired session
    print("Checking login status...")
    logged_in = False
    try:
        page.goto("https://www.binance.com/en/my/dashboard", wait_until="commit", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(3000)

    # Hit a private endpoint and check response
    try:
        resp = page.evaluate("""
            async () => {
                const r = await fetch('/bapi/futures/v1/private/future/copy-trade/home-page/user-info', {method:'POST', headers:{'content-type':'application/json'}, body:'{}'});
                const j = await r.json();
                return j.code;
            }
        """)
        logged_in = (resp == "000000")
        print(f"  Auth check: code={resp} → {'logged in' if logged_in else 'NOT logged in'}")
    except Exception as e:
        print(f"  Auth check failed: {e}")
        logged_in = False

    if not logged_in:
        print("\n⚠️  Not logged in — opening login page...")
        try:
            page.goto("https://accounts.binance.com/en/login", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        print(">>> Please log in to Binance in the browser window.")
        print(">>> Press ENTER here once you are on your dashboard: ", end="", flush=True)
        input()
        time.sleep(3)
        pages = ctx.pages
        page = pages[-1] if pages else page
        page.on("request", on_request)
        page.on("response", on_response)
        # Verify login succeeded
        try:
            page.goto("https://www.binance.com/en/my/dashboard", wait_until="commit", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        print("✅ Proceeding...") 

    # Execute the copy trade
    success = do_copy_trade(page)

    # Save captured endpoints
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(captured, f, indent=2, ensure_ascii=False)

    print(f"\n📁 Saved {len(captured)} BAPI endpoints to {OUTPUT_FILE}")
    print("\nKey endpoints captured:")
    for e in captured:
        if e["method"] == "POST" and "copy" in e["url"].lower():
            print(f"  POST {e['url']}")
            if e.get("request_body"):
                print(f"       Body: {e['request_body'][:300]}")
            resp = e.get("response_body")
            if isinstance(resp, dict):
                print(f"       Code: {resp.get('code')} | Data: {json.dumps(resp.get('data', ''))[:200]}")

    print("\nKeeping browser open for 10s...")
    page.wait_for_timeout(10000)
    ctx.close()

print("\nDone.")
