# -*- coding: utf-8 -*-
"""
Fully automated copy trade executor.
- Accepts OneTrust cookie consent BEFORE clicking Copy (prevents Privacy modal blocking)
- Keeps background networking enabled so Binance JWT tokens stay refreshed
- Handles login prompt if session expired
"""
import time
from playwright.sync_api import sync_playwright

CHROME_EXE = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
SESSION_DIR = r"C:\BinanceBot\playwright_session"

TRADERS = [
    ("黑皮哥哥", "https://www.binance.com/en/copy-trading/lead-details/4878524971286521088?timeRange=30D"),
]
AMOUNT = "100"


def accept_cookie_consent(page):
    """Accept OneTrust cookie consent — MUST happen before clicking Copy or the Privacy
    Preference Center modal opens with aria-modal=true and traps all pointer events."""
    # Standard bottom banner (first page load)
    for sel in ["#onetrust-accept-btn-handler",
                ".onetrust-accept-btn-handler",
                "button[id*='onetrust'][id*='accept']"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                el.click()
                page.wait_for_timeout(1000)
                print("  ✅ Cookie consent accepted (banner)")
                return True
        except Exception:
            pass

    # Privacy Preference Center modal (already open)
    try:
        modal = page.locator('[aria-label="Privacy Preference Center"]')
        if modal.is_visible(timeout=500):
            for sel in ["#accept-recommended-btn-handler",
                        ".save-preference-btn-handler",
                        "button:has-text('Accept All')",
                        "button:has-text('Confirm My Choices')"]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=500):
                        btn.click()
                        page.wait_for_timeout(1000)
                        print("  ✅ Cookie consent accepted (privacy modal)")
                        return True
                except Exception:
                    pass
    except Exception:
        pass

    return False  # already accepted or not present


def find_copy_button(page):
    """Find the exact 'Copy' button (not 'Mock Copy')."""
    for btn in page.locator("button").all():
        try:
            if btn.inner_text().strip() == "Copy" and btn.is_visible():
                return btn
        except Exception:
            pass
    return None


def do_trade(page, trader_name, url):
    print(f"\n{'='*50}")
    print(f"Trading: {trader_name}")
    print(f"URL: {url}")

    try:
        page.goto(url, wait_until="commit", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(4000)

    # CRITICAL: Accept cookie consent BEFORE clicking Copy
    # Without this, clicking Copy triggers the OneTrust Privacy Preference Center
    # modal (aria-modal=true) which traps all pointer events and blocks the copy dialog.
    accept_cookie_consent(page)

    # Verify login
    has_deposit = page.evaluate("() => !!document.querySelector('.deposit-btn')")
    print(f"  Logged in: {has_deposit}")
    if not has_deposit:
        print("  ERROR: Not logged in!")
        return False

    # Find Copy button
    copy_btn = find_copy_button(page)
    if not copy_btn:
        print("  ERROR: Copy button not found!")
        return False

    box = copy_btn.bounding_box()
    print(f"  Copy button at: {box}")

    # Hover first, then click
    copy_btn.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    copy_btn.hover()
    page.wait_for_timeout(800)
    page.screenshot(path=rf"C:\BinanceBot\trade_{trader_name}_hover.png")

    print("  Clicking Copy...")
    copy_btn.click()
    page.wait_for_timeout(2000)

    # Safety net: if Privacy modal still appeared, accept and retry once
    try:
        modal = page.locator('[aria-label="Privacy Preference Center"]')
        if modal.is_visible(timeout=1000):
            print("  Privacy modal appeared post-click — accepting and retrying...")
            accept_cookie_consent(page)
            page.wait_for_timeout(1000)
            copy_btn2 = find_copy_button(page)
            if copy_btn2:
                copy_btn2.click()
                page.wait_for_timeout(2000)
    except Exception:
        pass

    # Wait for copy dialog
    print("  Waiting for copy dialog...")
    dialog = None
    for sel in [".bn-modal", ".bn-drawer",
                "[class*='copy-amount']", "[class*='copyModal']",
                "[role='dialog']:not([aria-label='Privacy Preference Center'])"]:
        try:
            page.wait_for_selector(sel, timeout=5000, state="visible")
            label = page.evaluate(f"""() => {{
                const el = document.querySelector("{sel}");
                return el ? el.getAttribute('aria-label') : null;
            }}""")
            if label == "Privacy Preference Center":
                continue
            print(f"  Dialog found via: {sel}")
            dialog = page.locator(sel).first
            break
        except Exception:
            pass

    page.screenshot(path=rf"C:\BinanceBot\trade_{trader_name}_dialog.png")

    if dialog is None:
        print("  No dialog found - dumping visible DOM changes...")
        # Dump ALL visible inputs
        inputs = page.locator("input").all()
        print(f"  Inputs ({len(inputs)}):")
        for inp in inputs:
            try:
                if inp.is_visible():
                    print(f"    placeholder='{inp.get_attribute('placeholder')}' type='{inp.get_attribute('type')}'")
            except Exception:
                pass
        # Dump all buttons
        btns = page.locator("button").all()
        vis_btns = []
        for b in btns:
            try:
                if b.is_visible():
                    vis_btns.append(b.inner_text().strip())
            except Exception:
                pass
        print(f"  Visible buttons: {vis_btns}")
        return False

    # Find amount input
    amount_input = None
    for sel in ["input[placeholder*='Amount']", "input[placeholder*='amount']",
                "input[placeholder*='USDT']", "input[type='number']",
                "input[inputmode='decimal']", "[role='dialog'] input", ".bn-modal input"]:
        try:
            inp = page.locator(sel).first
            if inp.is_visible(timeout=2000):
                amount_input = inp
                print(f"  Amount input via: {sel}")
                break
        except Exception:
            pass

    if amount_input is None:
        print("  ERROR: No amount input found in dialog!")
        # Try getting dialog HTML
        try:
            print(f"  Dialog HTML: {dialog.inner_html()[:500]}")
        except Exception:
            pass
        return False

    # Fill amount
    print(f"  Filling amount: {AMOUNT}")
    amount_input.click()
    amount_input.select_all()
    amount_input.fill(AMOUNT)
    page.wait_for_timeout(1000)
    page.screenshot(path=rf"C:\BinanceBot\trade_{trader_name}_filled.png")
    print(f"  Amount filled. Screenshot saved.")

    # Find and click Confirm
    confirm_btn = None
    for sel in ["button:has-text('Confirm')", "button:has-text('Copy Now')",
                "button:has-text('Start Copy')", "button:has-text('OK')"]:
        try:
            btn = page.locator(sel).last  # use last to avoid header buttons
            if btn.is_visible(timeout=2000):
                confirm_btn = btn
                print(f"  Confirm button via: {sel}")
                break
        except Exception:
            pass

    if confirm_btn is None:
        print("  ERROR: No confirm button found!")
        btns = page.locator("button").all()
        for b in btns:
            try:
                if b.is_visible():
                    print(f"    btn: '{b.inner_text().strip()}'")
            except Exception:
                pass
        return False

    print(f"  Clicking Confirm for {trader_name}...")
    confirm_btn.click()
    page.wait_for_timeout(5000)
    page.screenshot(path=rf"C:\BinanceBot\trade_{trader_name}_confirmed.png")
    print(f"  ✅ Confirmed! Screenshot: trade_{trader_name}_confirmed.png")
    return True


with sync_playwright() as pw:
    # ignore_default_args removes --disable-background-networking and --disable-sync
    # which Playwright adds by default — those flags prevent Binance JWT token refresh
    # and cause "Login status expired" errors between page navigations.
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=SESSION_DIR,
        executable_path=CHROME_EXE,
        headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--no-sandbox",
              "--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 900},
        timeout=30000,
    )
    page = ctx.new_page()

    # Check if already logged in
    print("Checking login status...")
    try:
        page.goto("https://www.binance.com/en/copy-trading", wait_until="commit", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(4000)

    has_deposit = page.evaluate("() => !!document.querySelector('.deposit-btn')")

    if not has_deposit:
        print("\n⚠️  Not logged in. Opening login page...")
        page.goto("https://accounts.binance.com/en/login", wait_until="domcontentloaded", timeout=30000)
        print("\n>>> Please log in to Binance in the browser window that opened.")
        input(">>> Once you are fully logged in and on your dashboard, press ENTER here: ")

        # After login Binance redirects; recover the active page from context
        time.sleep(2)
        pages = ctx.pages
        page = pages[-1] if pages else page

        # Navigate to copy trading to confirm login
        try:
            page.goto("https://www.binance.com/en/copy-trading", wait_until="commit", timeout=30000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(4000)
        except Exception:
            page = ctx.pages[-1]
            page.wait_for_timeout(4000)

        has_deposit = page.evaluate("() => !!document.querySelector('.deposit-btn')")
        if not has_deposit:
            print("❌ Still not logged in. Exiting.")
            ctx.close()
            exit(1)
    print("✅ Logged in!\n")

    # Execute trades
    results = {}
    for trader_name, url in TRADERS:
        ok = do_trade(page, trader_name, url)
        results[trader_name] = ok
        if ok:
            print(f"✅ {trader_name}: TRADE EXECUTED")
        else:
            print(f"❌ {trader_name}: TRADE FAILED")
        time.sleep(2)

    print("\n" + "="*50)
    print("RESULTS:")
    for name, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {trader_name}: {'Success' if ok else 'Failed'}")

    print("\nKeeping browser open for 10s...")
    page.wait_for_timeout(10000)
    ctx.close()
