"""
capture_session.py
==================
ONE-TIME SETUP SCRIPT.

Opens Chrome with your saved session. If not logged in, prompts you to log in.
Then navigates to a trader page and waits for you to click Copy manually.
Intercepts the BAPI copy-trade POST request and saves:
  - cookies -> C:\BinanceBot\session_cookies.json
  - endpoints -> C:\BinanceBot\bapi_endpoints.json  (overwritten with real data)

After this runs successfully, binance_api.py can do everything headlessly.
"""

import json, time, re
from pathlib import Path
from playwright.sync_api import sync_playwright

CHROME_EXE = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
SESSION_DIR = r"C:\BinanceBot\playwright_session"
COOKIES_FILE = r"C:\BinanceBot\session_cookies.json"
ENDPOINTS_FILE = r"C:\BinanceBot\bapi_endpoints.json"

# Trader to navigate to (for easier Copy button clicking)
TRADER_URL = "https://www.binance.com/en/copy-trading/lead-details/5010515263519011585?timeRange=30D"

captured_endpoints = []
copy_endpoint = None
stop_endpoint = None

INTERESTING = re.compile(
    r"copy-trade|copyTrade|start-copy|stop-copy|follow|unfollow",
    re.IGNORECASE
)

def on_request(req):
    if INTERESTING.search(req.url) and req.method == "POST":
        try:
            body = req.post_data_json
        except Exception:
            body = req.post_data
        print(f"\n  [REQ] {req.method} {req.url}")
        if body:
            print(f"        body: {json.dumps(body)[:300]}")

def on_response(resp):
    global copy_endpoint, stop_endpoint
    if INTERESTING.search(resp.url):
        try:
            body = resp.json()
        except Exception:
            body = None
        print(f"  [RES] {resp.status} {resp.url}")
        if body:
            print(f"        resp: {json.dumps(body)[:300]}")
        captured_endpoints.append({
            "method": resp.request.method,
            "url": resp.url,
            "status": resp.status,
            "request_body": resp.request.post_data,
            "response_body": body,
        })
        # Tag the interesting ones
        url_lower = resp.url.lower()
        if "start" in url_lower or ("copy" in url_lower and "stop" not in url_lower and resp.request.method == "POST"):
            copy_endpoint = {
                "url": resp.url,
                "body": resp.request.post_data,
                "status": resp.status,
            }
        if "stop" in url_lower and resp.request.method == "POST":
            stop_endpoint = {
                "url": resp.url,
                "body": resp.request.post_data,
                "status": resp.status,
            }


with sync_playwright() as pw:
    print("Launching Chrome with saved session...")
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=SESSION_DIR,
        executable_path=CHROME_EXE,
        headless=False,
        args=[
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
        viewport={"width": 1280, "height": 900},
        timeout=30000,
    )

    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_request)
    page.on("response", on_response)

    # ── Check login ──────────────────────────────────────────────────────────
    print("Checking login...")
    try:
        page.goto("https://www.binance.com/en/my/dashboard", wait_until="commit", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(3000)

    try:
        code = page.evaluate("""
            async () => {
                const r = await fetch('/bapi/accounts/v1/private/account/user/base-detail',
                    {method:'POST', headers:{'content-type':'application/json'}, body:'{}'});
                const j = await r.json();
                return j.code;
            }
        """)
        logged_in = (code == "000000")
    except Exception:
        logged_in = False

    print(f"  → logged_in={logged_in} (code={code if not logged_in else '000000'})")

    if not logged_in:
        print("\n⚠️  Session expired. Opening login page...")
        try:
            page.goto("https://accounts.binance.com/en/login", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        input("\n>>> Log in to Binance in the Chrome window, then press ENTER here: ")
        time.sleep(2)
        page = ctx.pages[-1] if ctx.pages else page
        page.on("request", on_request)
        page.on("response", on_response)

    # ── Navigate to trader page ──────────────────────────────────────────────
    print(f"\nNavigating to trader page...")
    try:
        page.goto(TRADER_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"  Navigation warning: {e}")
    page.wait_for_timeout(3000)

    print("\n" + "="*60)
    print("READY. In the Chrome window:")
    print("  1. Click the Copy button")
    print("  2. Fill in the amount (e.g. 100 USDT)")
    print("  3. Click Confirm / Start Copy")
    print("  4. (Optional) then click Stop Copy to also capture that endpoint")
    print("\nThis script is intercepting all network calls.")
    print("="*60)
    input("\nPress ENTER here when you have finished clicking Confirm: ")

    # ── Extract cookies via Playwright API (plaintext, no DPAPI needed) ──────
    print("\nExtracting session cookies...")
    all_cookies = ctx.cookies()
    binance_cookies = [c for c in all_cookies if "binance.com" in c.get("domain", "")]
    
    with open(COOKIES_FILE, "w") as f:
        json.dump(binance_cookies, f, indent=2)
    print(f"✅ Saved {len(binance_cookies)} Binance cookies → {COOKIES_FILE}")

    # ── Save captured endpoints ──────────────────────────────────────────────
    with open(ENDPOINTS_FILE, "w") as f:
        json.dump(captured_endpoints, f, indent=2)
    print(f"✅ Saved {len(captured_endpoints)} BAPI calls → {ENDPOINTS_FILE}")

    if copy_endpoint:
        print(f"\n🎯 COPY endpoint detected:")
        print(f"   URL:  {copy_endpoint['url']}")
        print(f"   Body: {copy_endpoint['body']}")
    if stop_endpoint:
        print(f"\n🎯 STOP endpoint detected:")
        print(f"   URL:  {stop_endpoint['url']}")
        print(f"   Body: {stop_endpoint['body']}")

    if not copy_endpoint and not stop_endpoint:
        print("\n⚠️  No copy-trade endpoints captured. Make sure you clicked Copy+Confirm in the browser.")

    ctx.close()
    print("\nDone. You can now use binance_api.py for all future trades (no browser needed).")
