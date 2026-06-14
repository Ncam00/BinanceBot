#!/usr/bin/env python3
"""
One-time Binance login helper for the copy-trading bot.

Opens a visible Chrome window using the SAME persistent profile the bot uses
(C:\\BinanceBot\\playwright_session). You log in by hand (including 2FA / device
verification). Once the script confirms the action-level session is live,
it saves cookies and exits — the bot can then run unattended for days/weeks.

Usage:
    C:\\python314\\python.exe -u C:\\BinanceBot\\login_helper.py
"""

from __future__ import annotations

import json
import os
import sys
import time

DATA_DIR     = r"C:\BinanceBot\playwright_session"
COOKIE_FILE  = r"C:\BinanceBot\session_cookies.json"
CHROME_PATH  = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

# A real action page (lead-details) — if Binance does NOT redirect this to the
# login page, the session is at action-level and the bot will work.
TEST_ACTION_URL = (
    "https://www.binance.com/en/copy-trading/lead-details/"
    "4858297407050513153?timeRange=30D"
)

# JS used by the bot to verify auth — kept in sync with copy_monitor._AUTH_CHECK_JS
AUTH_CHECK_JS = """
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


def main() -> int:
    from playwright.sync_api import sync_playwright

    os.makedirs(DATA_DIR, exist_ok=True)
    print("=" * 70)
    print(" Binance Login Helper")
    print("=" * 70)
    print(f" Profile:        {DATA_DIR}")
    print(f" Chrome:         {CHROME_PATH}")
    print(f" Cookie backup:  {COOKIE_FILE}")
    print("=" * 70)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=DATA_DIR,
            headless=False,
            executable_path=CHROME_PATH,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
            viewport=None,
        )

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("\n[1/3] Opening Binance login page…")
        try:
            page.goto(
                "https://accounts.binance.com/en/login",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except Exception as e:
            print(f"      Navigation warning: {e}")

        print("\n>>> Please log in now. Complete 2FA / device verification.")
        print(">>> The script is waiting for you (up to 10 minutes).")
        print(">>> DO NOT close the browser window.\n")

        try:
            page.wait_for_function(
                "() => !window.location.hostname.includes('accounts.binance.com')",
                timeout=600_000,
            )
        except Exception:
            print("\n[ERROR] Timed out waiting for login (10 min). Aborting.")
            ctx.close()
            return 2

        print("[2/3] Login redirect detected — letting cookies settle (15s)…")
        time.sleep(15)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        # Verify basic auth
        basic_ok = False
        for i in range(3):
            try:
                if page.evaluate(AUTH_CHECK_JS):
                    basic_ok = True
                    break
            except Exception:
                pass
            time.sleep(5)
        if not basic_ok:
            print("[ERROR] Basic auth check failed. The session does not look logged in.")
            print("        Try again, or finish any pending security challenge in the window.")
            ctx.close()
            return 3
        print("       Basic auth OK.")

        # The real test — can we open an action page WITHOUT being bounced to login?
        print("\n[3/3] Verifying action-level session (lead-details page)…")
        try:
            page.goto(TEST_ACTION_URL, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(6)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
        except Exception as e:
            print(f"      Navigation warning: {e}")

        final_host = ""
        try:
            final_host = page.evaluate("() => window.location.hostname") or ""
        except Exception:
            pass
        final_url = ""
        try:
            final_url = page.url or ""
        except Exception:
            pass

        if "accounts.binance.com" in final_host or "/login" in final_url:
            print("[ERROR] Action page redirected back to login.")
            print(f"        Final URL: {final_url}")
            print("        Binance is requiring extra verification. Complete any prompts in the")
            print("        window (e.g. device verification email/SMS), then run this script again.")
            ctx.close()
            return 4

        print(f"       Action page loaded: {final_url}")
        print("       Action-level session confirmed.")

        # Persist cookies as a backup (the persistent profile holds them too)
        try:
            cookies = ctx.cookies()
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            print(f"\n       Saved {len(cookies)} cookies to {COOKIE_FILE}")
        except Exception as e:
            print(f"       [WARN] Could not save cookies: {e}")

        print("\n" + "=" * 70)
        print(" SUCCESS — you are logged in.")
        print(" You can now close this window. The bot will reuse this session.")
        print("=" * 70)
        print("\nClosing browser in 5 seconds…")
        time.sleep(5)
        ctx.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
