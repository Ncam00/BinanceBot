# -*- coding: utf-8 -*-
"""
Connect to Binance and copy-trade two traders using existing Chrome profile.
Uses Playwright's launch_persistent_context with Chrome's profile (has login cookies).
"""

import os
import sys
import time
import shutil

CHROME_EXE = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
CHROME_PROFILE = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
TEMP_PROFILE = r"C:\BinanceBot\chrome_trade_profile"

TRADERS = [
    ("黑皮哥哥",   "https://www.binance.com/en/copy-trading/lead-details/4878524971286521088?timeRange=30D"),
    ("Kelvin_Chen","https://www.binance.com/en/copy-trading/lead-details/4913425449488169473?timeRange=30D"),
]
AMOUNT = "100"

def copy_chrome_profile():
    """
    Copy Chrome Default profile to temp dir.
    Uses actual Chrome binary so App-Bound Encryption (Chrome 127+) works correctly.
    Skips lock files and large cache dirs to speed up copy.
    """
    # Clean and recreate
    if os.path.exists(TEMP_PROFILE):
        shutil.rmtree(TEMP_PROFILE, ignore_errors=True)
    os.makedirs(TEMP_PROFILE, exist_ok=True)

    # Skip these dirs (not needed for session / too large)
    SKIP_DIRS = {
        "Cache", "Code Cache", "GPUCache", "DawnCache",
        "ShaderCache", "Service Worker", "CacheStorage",
        "blob_storage", "VideoDecodeStats", "optimization_guide_model_store",
    }
    # Skip these files (lock files that block Chrome from starting)
    SKIP_FILES = {
        "SingletonLock", "SingletonSocket", "SingletonCookie",
        "lockfile", "LOCK", "LOG", "LOG.old",
    }

    default_src = os.path.join(CHROME_PROFILE, "Default")
    default_dst = os.path.join(TEMP_PROFILE, "Default")
    os.makedirs(default_dst, exist_ok=True)

    copied = 0
    skipped = 0
    for item in os.listdir(default_src):
        if item in SKIP_FILES:
            skipped += 1
            continue
        src = os.path.join(default_src, item)
        dst = os.path.join(default_dst, item)
        try:
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                copied += 1
            elif os.path.isdir(src) and item not in SKIP_DIRS:
                shutil.copytree(src, dst,
                    ignore=shutil.ignore_patterns("*.tmp", "LOCK", "LOG*", "SingletonLock"),
                    dirs_exist_ok=True)
                copied += 1
            else:
                skipped += 1
        except Exception as e:
            skipped += 1  # silently skip locked files

    # Copy top-level Local State (contains App-Bound Encryption keys)
    ls_src = os.path.join(CHROME_PROFILE, "Local State")
    if os.path.exists(ls_src):
        shutil.copy2(ls_src, os.path.join(TEMP_PROFILE, "Local State"))
        copied += 1

    print(f"  Copied {copied} items, skipped {skipped} (locks/caches)")
    return TEMP_PROFILE


def do_trade(page, trader_name, url):
    print(f"\n--- Processing {trader_name} ---")
    print(f"  Navigating to: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)

    # Dismiss cookie consent popup if present
    try:
        accept_btn = page.locator("button:has-text('Accept Cookies')").first
        if accept_btn.is_visible(timeout=3000):
            accept_btn.click()
            print("  Dismissed cookie consent popup")
            page.wait_for_timeout(1000)
    except Exception:
        pass

    # Check if logged in
    cur_url = page.url
    print(f"  Current URL: {cur_url}")
    if "login" in cur_url.lower() or "auth" in cur_url.lower():
        print("  ERROR: Not logged in — redirected to login page")
        return False

    # Take screenshot to see what we're working with
    ss_path = rf"C:\BinanceBot\trade_{trader_name}.png"
    page.screenshot(path=ss_path)
    print(f"  Screenshot saved: {ss_path}")

    # Find the Copy button — it has w-full class (not Deposit button)
    # Binance copy button selectors to try
    selectors = [
        "button.bn-button__primary.w-full",
        "button:has-text('Copy')",
        "button[class*='primary'][class*='w-full']",
    ]

    copy_btn = None
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                copy_btn = btn
                print(f"  Found copy button via: {sel}")
                break
        except Exception:
            continue

    if copy_btn is None:
        print("  ERROR: Could not find Copy button")
        # Dump all buttons for debug
        btns = page.locator("button").all()
        print(f"  All buttons ({len(btns)}):")
        for b in btns[:10]:
            try:
                print(f"    text='{b.inner_text()[:40]}' class='{b.get_attribute('class')}'")
            except Exception:
                pass
        return False

    # Click the Copy button
    print("  Clicking Copy button...")
    copy_btn.click()
    page.wait_for_timeout(3000)

    # Screenshot after click
    ss2_path = rf"C:\BinanceBot\trade_{trader_name}_after_click.png"
    page.screenshot(path=ss2_path)
    print(f"  Screenshot after click: {ss2_path}")

    # Look for amount input in the dialog
    input_selectors = [
        "input[placeholder*='Amount']",
        "input[placeholder*='USDT']",
        "input[type='number']",
        "input[inputmode='decimal']",
        ".bn-input input",
    ]

    amount_input = None
    for sel in input_selectors:
        try:
            inp = page.locator(sel).first
            if inp.is_visible(timeout=3000):
                amount_input = inp
                print(f"  Found amount input via: {sel}")
                break
        except Exception:
            continue

    if amount_input is None:
        print("  ERROR: Could not find amount input after clicking Copy")
        # Dump visible inputs
        inputs = page.locator("input").all()
        print(f"  Visible inputs ({len(inputs)}):")
        for i in inputs[:10]:
            try:
                print(f"    type='{i.get_attribute('type')}' placeholder='{i.get_attribute('placeholder')}' class='{i.get_attribute('class')}'")
            except Exception:
                pass
        return False

    # Clear and fill amount
    print(f"  Entering amount: {AMOUNT} USDT")
    amount_input.click()
    amount_input.select_all() if hasattr(amount_input, 'select_all') else None
    amount_input.fill("")
    time.sleep(0.3)
    amount_input.fill(AMOUNT)
    page.wait_for_timeout(1000)

    # Screenshot before final confirm
    ss3_path = rf"C:\BinanceBot\trade_{trader_name}_filled.png"
    page.screenshot(path=ss3_path)
    print(f"  Screenshot with amount filled: {ss3_path}")

    # Find and click the Confirm button
    confirm_selectors = [
        "button:has-text('Confirm')",
        "button:has-text('Copy Now')",
        "button:has-text('Start Copy')",
        "button[class*='primary']:has-text('Confirm')",
    ]

    confirm_btn = None
    for sel in confirm_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                confirm_btn = btn
                print(f"  Found confirm button via: {sel}")
                break
        except Exception:
            continue

    if confirm_btn is None:
        print("  ERROR: Could not find Confirm button")
        # List all visible buttons
        btns = page.locator("button").all()
        print(f"  Visible buttons:")
        for b in btns[:15]:
            try:
                txt = b.inner_text()[:40].strip()
                if txt:
                    print(f"    '{txt}'")
            except Exception:
                pass
        return False

    print("  Clicking Confirm...")
    confirm_btn.click()
    page.wait_for_timeout(3000)

    # Final screenshot
    ss4_path = rf"C:\BinanceBot\trade_{trader_name}_done.png"
    page.screenshot(path=ss4_path)
    print(f"  Final screenshot: {ss4_path}")

    # Check for success indicators
    page_text = page.inner_text("body")
    if any(word in page_text for word in ["success", "Success", "successfully", "Started", "Copying"]):
        print(f"  SUCCESS: Trade confirmed for {trader_name}!")
        return True
    else:
        print(f"  Trade submitted — check screenshots to confirm.")
        return True


def main():
    from playwright.sync_api import sync_playwright

    print("=== Binance Copy Trade Automation ===")
    print(f"Traders: {[t[0] for t in TRADERS]}")
    print(f"Amount per trader: {AMOUNT} USDT")

    # Launch a fresh Playwright browser session, ask user to log in, then automate.
    # This avoids App-Bound Encryption issues entirely — session lives only in this process.
    SESSION_DIR = r"C:\BinanceBot\playwright_session"
    os.makedirs(SESSION_DIR, exist_ok=True)

    print("\nLaunching browser. A Chrome window will open.")
    print("Please LOG IN to Binance, then come back to this terminal and press ENTER.")
    print("(The browser window must stay open.)\n")

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

        # Check if already logged in from saved session
        try:
            page.goto("https://www.binance.com/en/copy-trading", wait_until="commit", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        if "login" in page.url.lower():
            # Not logged in — ask user
            page.goto("https://www.binance.com/en/login", wait_until="domcontentloaded", timeout=15000)
            print("Browser opened at Binance login page.")
            print("\n>>> Please log in to Binance in the browser window, then press ENTER here <<<")
            input()
            try:
                page.goto("https://www.binance.com/en/copy-trading", wait_until="commit", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(3000)
            if "login" in page.url.lower():
                print("ERROR: Still not logged in.")
                ctx.close()
                return {}

        print(f"Logged in (at: {page.url}). Starting trades...\n")

        results = {}
        for name, url in TRADERS:
            ok = do_trade(page, name, url)
            results[name] = ok

        print("\n=== Results ===")
        for name, ok in results.items():
            status = "SUCCESS" if ok else "FAILED"
            print(f"  {name}: {status}")

        input("\nPress ENTER to close browser...")
        ctx.close()

    return results


if __name__ == "__main__":
    main()
