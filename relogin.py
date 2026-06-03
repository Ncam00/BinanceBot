# -*- coding: utf-8 -*-
"""
Re-login script — opens Chrome with the playwright_session profile so
the user can log in to Binance. Session is saved automatically.
Run this, log in manually in the browser that appears, then press ENTER here.
"""
from playwright.sync_api import sync_playwright

CHROME_EXE = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
SESSION_DIR = r"C:\BinanceBot\playwright_session"
LOGIN_URL   = "https://accounts.binance.com/en/login"

print("Opening Binance login page...")
print("Please log in to your account in the browser that opens.")
print("Press ENTER here after you have fully logged in.\n")

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=SESSION_DIR,
        executable_path=CHROME_EXE,
        headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--no-sandbox"],
        viewport={"width": 1280, "height": 900},
        timeout=30000,
    )
    page = ctx.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

    input(">>> Log in to Binance in the browser, then press ENTER here to save session: ")

    # Verify login succeeded
    page.goto("https://www.binance.com/en/my/dashboard", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    if "/my/dashboard" in page.url or "/dashboard" in page.url:
        print(f"\n✅ Login confirmed! Current URL: {page.url}")
        print("Session saved to:", SESSION_DIR)
        print("You can now run do_copy_trades.py")
    else:
        print(f"\n⚠️  Not on dashboard. Current URL: {page.url}")
        print("You may not be fully logged in — please check and try again.")

    ctx.close()
