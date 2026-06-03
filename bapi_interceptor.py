# -*- coding: utf-8 -*-
"""
Phase 2: BAPI endpoint interceptor.
Opens Chrome, navigates to a trader page, intercepts ALL network requests
while you manually click Copy+Confirm and Stop Copy.
Saves captured endpoints to bapi_endpoints.json for use by binance_api.py.
"""
import json
import time
from playwright.sync_api import sync_playwright

CHROME_EXE = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
SESSION_DIR = r"C:\BinanceBot\playwright_session"
OUTPUT_FILE = r"C:\BinanceBot\bapi_endpoints.json"
TRADER_URL = "https://www.binance.com/en/copy-trading/lead-details/4878524971286521088?timeRange=30D"

captured = []  # list of {action, method, url, request_body, response_body, status}

INTERESTING_PATTERNS = [
    "copy-trade", "copyTrade", "copy_trade",
    "follower", "follow", "portfolio",
    "spot-copy", "future-copy",
    "lead-details",
]

def is_interesting(url):
    url_lower = url.lower()
    return any(p.lower() in url_lower for p in INTERESTING_PATTERNS)

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=SESSION_DIR,
        executable_path=CHROME_EXE,
        headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--no-sandbox",
              "--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--disable-background-networking", "--disable-sync",
                             "--disable-background-timer-throttling",
                             "--disable-backgrounding-occluded-windows"],
        viewport={"width": 1280, "height": 900},
        timeout=30000,
    )
    page = ctx.new_page()

    # Intercept requests + responses
    request_bodies = {}

    def on_request(req):
        if is_interesting(req.url):
            try:
                body = req.post_data
            except Exception:
                body = None
            request_bodies[req.url] = {"method": req.method, "body": body, "headers": dict(req.headers)}
            print(f"  → REQ {req.method} {req.url[:100]}")
            if body:
                print(f"       BODY: {body[:300]}")

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
                "request_headers": req_info.get("headers", {}),
                "response_body": body,
            }
            captured.append(entry)
            print(f"  ← RES {resp.status} {resp.url[:100]}")
            print(f"       RESP: {json.dumps(body)[:300]}")

    page.on("request", on_request)
    page.on("response", on_response)

    # Login check
    print("Checking login...")
    try:
        page.goto(TRADER_URL, wait_until="commit", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(4000)

    has_deposit = page.evaluate("() => !!document.querySelector('.deposit-btn')")
    if not has_deposit:
        page.goto("https://accounts.binance.com/en/login", wait_until="domcontentloaded", timeout=30000)
        input(">>> Log in, then press ENTER: ")
        try:
            page.goto(TRADER_URL, wait_until="commit", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(4000)

    print("\n✅ Ready. Browser is on the trader page.")
    print("=" * 60)
    print("ACTION 1: Click 'Copy', enter 100 USDT, click Confirm.")
    print("          Watch this terminal for captured API calls.")
    print("=" * 60)
    input("\n>>> Press ENTER when you have completed the Copy action: ")

    print("\n" + "=" * 60)
    print("ACTION 2 (optional): Click 'Stop Copy' to capture that endpoint too.")
    print("=" * 60)
    skip = input("\n>>> Press ENTER when done, or type 'skip' to skip Stop Copy: ")

    # Save all captured endpoints
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(captured, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Saved {len(captured)} endpoints to {OUTPUT_FILE}")
    print("\nSummary of captured endpoints:")
    for e in captured:
        print(f"  {e['method']} {e['url']}")
        if e.get("request_body"):
            print(f"    Body: {e['request_body'][:200]}")
        print(f"    Response code: {e.get('response_body', {}).get('code') if isinstance(e.get('response_body'), dict) else ''}")

    page.wait_for_timeout(5000)
    ctx.close()
