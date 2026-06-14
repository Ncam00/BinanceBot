# -*- coding: utf-8 -*-
"""
Semi-automated copy trade: opens each trader page, waits for manual Copy click,
detects success, then moves on. Updates copy_monitor.py on completion.
"""
import time
import re
from playwright.sync_api import sync_playwright

CHROME_EXE = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
SESSION_DIR = r"C:\BinanceBot\playwright_session"

TRADERS = [
    ("黑皮哥哥",   "https://www.binance.com/en/copy-trading/lead-details/4878524971286521088?timeRange=30D"),
    ("Kelvin_Chen","https://www.binance.com/en/copy-trading/lead-details/4913425449488169473?timeRange=30D"),
]

successful_trades = []
copy_api_hits = []

def wait_for_copy_success(page, trader_name, timeout_sec=120):
    """Wait for the copy trade to be confirmed — detect via button change or API response."""
    print(f"  Waiting up to {timeout_sec}s for copy confirmation...")

    # Listen for the copy-trade create/start API response
    result = {"done": False, "method": None}

    def on_response(resp):
        url = resp.url
        if any(x in url for x in ["copy-trade/follower", "copy-trade/start", "copy-trade/create",
                                   "spot-copy-trade/follower", "spot-copy-trade/start"]):
            try:
                body = resp.json()
                print(f"  API response [{resp.status}]: {url.split('/')[-1]}")
                print(f"  Body: {body}")
                if body.get("success") or body.get("code") == "000000":
                    result["done"] = True
                    result["method"] = "api"
            except Exception:
                pass

    page.on("response", on_response)

    start = time.time()
    while time.time() - start < timeout_sec:
        page.wait_for_timeout(2000)

        if result["done"]:
            print(f"  ✅ Copy confirmed via API!")
            return True

        # Check if button changed to "Stop Copy" or similar
        try:
            buttons = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('button'))
                    .filter(b => b.offsetParent !== null)
                    .map(b => b.innerText.trim())
                    .filter(t => t);
            }""")
            if any(t in ["Stop Copy", "Manage", "Unfollow", "Stop Copying"] for t in buttons):
                print(f"  ✅ Copy confirmed via button state change! Buttons: {buttons}")
                return True
            # Show countdown
            elapsed = int(time.time() - start)
            remaining = timeout_sec - elapsed
            if elapsed % 10 == 0 and elapsed > 0:
                print(f"  Still waiting... {remaining}s remaining. Visible buttons: {[b for b in buttons if b][:8]}")
        except Exception:
            pass

    print(f"  ⏰ Timeout — did not detect copy confirmation.")
    return False


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

    # Check login
    print("Checking login...")
    try:
        page.goto("https://www.binance.com/en/copy-trading", wait_until="commit", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(4000)

    has_deposit = page.evaluate("() => !!document.querySelector('.deposit-btn')")
    if not has_deposit:
        print("Not logged in — opening login page...")
        page.goto("https://accounts.binance.com/en/login", wait_until="domcontentloaded", timeout=30000)
        input(">>> Log in, then press ENTER: ")
        try:
            page.goto("https://www.binance.com/en/copy-trading", wait_until="commit", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(4000)
        has_deposit = page.evaluate("() => !!document.querySelector('.deposit-btn')")
        if not has_deposit:
            print("Still not logged in — exiting.")
            ctx.close()
            exit(1)

    print("✅ Logged in!\n")

    for trader_name, url in TRADERS:
        print("=" * 60)
        print(f"TRADER: {trader_name}")
        print(f"Navigating to: {url}")
        try:
            page.goto(url, wait_until="commit", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        print(f"\n>>> ACTION REQUIRED: In the Chrome window, click the COPY button,")
        print(f">>>   enter 100 USDT, and click Confirm.")
        print(f">>> The script will auto-detect when done (up to 2 minutes).")
        print(f">>> OR press ENTER here to skip this trader.\n")

        # Run detection in background while user works
        import threading
        skip_flag = {"skip": False}

        def wait_input():
            input()
            skip_flag["skip"] = True

        t = threading.Thread(target=wait_input, daemon=True)
        t.start()

        ok = False
        start = time.time()
        while time.time() - start < 120:
            if skip_flag["skip"]:
                print(f"  Skipped {trader_name}.")
                break

            page.wait_for_timeout(2000)

            # Check button state
            try:
                buttons = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('button'))
                        .filter(b => b.offsetParent !== null)
                        .map(b => b.innerText.trim())
                        .filter(t => t);
                }""")
                if any(t in ["Stop Copy", "Manage", "Unfollow", "Stop Copying"] for t in buttons):
                    print(f"  ✅ {trader_name}: Copy confirmed! (button changed to: {[b for b in buttons if 'Stop' in b or 'Manage' in b or 'Unfollow' in b]})")
                    ok = True
                    break
            except Exception:
                pass

            # Check for API success via page evaluation
            api_success = page.evaluate("""() => {
                // Check if we're now copying (look for stop/manage button)
                const buttons = Array.from(document.querySelectorAll('button'));
                return buttons.some(b => ['Stop Copy', 'Manage', 'Unfollow', 'Stop Copying'].includes(b.innerText.trim()));
            }""")
            if api_success:
                print(f"  ✅ {trader_name}: Copy confirmed!")
                ok = True
                break

        if ok:
            successful_trades.append(trader_name)

        time.sleep(2)

    print("\n" + "=" * 60)
    print("RESULTS:")
    for name, _ in TRADERS:
        status = "✅ Success" if name in successful_trades else "❌ Not confirmed"
        print(f"  {name}: {status}")

    if successful_trades:
        print(f"\n✅ Updating copy_monitor.py with: {successful_trades}")
        monitor_path = r"C:\BinanceBot\copy_monitor.py"
        with open(monitor_path, "r", encoding="utf-8") as f:
            content = f.read()

        entries = ", ".join(f'"{n}": 100.0' for n in successful_trades)
        new_active = f"ACTIVE_COPIES = {{{entries}}}"
        # Replace the ACTIVE_COPIES line
        import re
        content = re.sub(r'ACTIVE_COPIES\s*(?::\s*dict\s*)?\=\s*\{[^}]*\}', new_active, content)
        with open(monitor_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Updated ACTIVE_COPIES = {{{entries}}}")
    else:
        print("\nNo trades confirmed — copy_monitor.py not updated.")

    print("\nKeeping browser open 15s...")
    page.wait_for_timeout(15000)
    ctx.close()
