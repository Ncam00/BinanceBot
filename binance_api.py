"""
binance_api.py
==============
Headless Playwright client for Binance copy trading.
Uses the saved session at C:\\BinanceBot\\playwright_session (no login needed
as long as cookies haven't expired — typically ~30 days).

Works by opening Chromium in HEADLESS mode, navigating to binance.com,
and using page.evaluate() to make authenticated fetch() calls from inside
the browser — so all fingerprint/nonce headers are handled automatically.

Usage from VS Code terminal:
    python binance_api.py status
    python binance_api.py start  <leadPortfolioId> <amount_usdt>
    python binance_api.py stop   <copyPortfolioId>

Or import and call directly:
    from binance_api import BinanceAPI
    with BinanceAPI() as api:
        api.start_copy("5010515263519011585", 100)
        api.stop_copy("5075159579394172161")
"""

import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

CHROME_EXE   = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
COOKIES_FILE = Path(r"C:\BinanceBot\session_cookies.json")
BASE         = "https://www.binance.com"

# JS helper — makes an authenticated POST from inside the browser context
_FETCH_JS = """
async (args) => {
    const r = await fetch(args.url, {
        method: 'POST',
        credentials: 'include',
        headers: {
            'content-type': 'application/json',
            'clienttype': 'web',
        },
        body: JSON.stringify(args.body),
    });
    try { return await r.json(); }
    catch(e) { return {_status: r.status, _text: await r.text()}; }
}
"""


class BinanceAPI:
    def __init__(self):
        if not COOKIES_FILE.exists():
            raise FileNotFoundError(
                f"No cookies at {COOKIES_FILE} — run capture_session.py first."
            )
        self._pw   = None
        self._ctx  = None
        self._page = None

    def __enter__(self):
        self._pw  = sync_playwright().start()
        # Use a plain (non-persistent) context so we can inject cookies cleanly
        self._ctx = self._pw.chromium.launch(
            executable_path=CHROME_EXE,
            headless=True,
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        ).new_context(
            viewport={"width": 1280, "height": 900},
        )
        # Inject saved cookies BEFORE navigating
        with open(COOKIES_FILE) as f:
            raw = json.load(f)
        cookies = []
        for c in raw:
            entry = {
                "name":   c["name"],
                "value":  c["value"],
                "domain": c.get("domain", ".binance.com"),
                "path":   c.get("path", "/"),
            }
            if c.get("secure"):
                entry["secure"] = True
            if c.get("sameSite") in ("Strict", "Lax", "None"):
                entry["sameSite"] = c["sameSite"]
            cookies.append(entry)
        self._ctx.add_cookies(cookies)
        print(f"  Injected {len(cookies)} cookies")

        self._page = self._ctx.new_page()
        try:
            self._page.goto(
                BASE + "/en/copy-trading/spot",
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception:
            pass
        self._page.wait_for_timeout(3000)
        return self

    def __exit__(self, *_):
        try:
            if self._ctx:
                self._ctx.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def _post(self, path: str, body: dict) -> dict:
        data = self._page.evaluate(
            _FETCH_JS,
            {"url": BASE + path, "body": body},
        )
        code = data.get("code", "?") if isinstance(data, dict) else "?"
        if code in ("100001005", "100002001"):
            raise RuntimeError(
                "Session expired — run capture_session.py to log in again."
            )
        return data

    # ── Public methods ────────────────────────────────────────────────────────

    def check_login(self) -> bool:
        try:
            data = self._post(
                "/bapi/futures/v1/private/future/spot-copy-trade/account/user-summary-info",
                {},
            )
            ok = isinstance(data, dict) and data.get("code") == "000000"
            print(f"  Login: {'OK' if ok else 'failed'} (code={data.get('code') if isinstance(data, dict) else '?'})")
            return ok
        except RuntimeError as e:
            print(f"  {e}")
            return False

    def list_copies(self) -> list:
        """Returns list of lead portfolio IDs you are currently copying."""
        data = self._post(
            "/bapi/futures/v1/private/future/spot-copy-trade/account/user-summary-info",
            {},
        )
        if isinstance(data, dict) and data.get("code") == "000000":
            ids = (data.get("data") or {}).get("copyLeadPortfolioIds") or []
            return ids
        return []

    def start_copy(self, lead_portfolio_id: str, amount_usdt: float, profit_share: int = 10) -> dict:
        """Start copying a trader. Returns API response dict."""
        print(f"  start_copy: leadPortfolioId={lead_portfolio_id}  amount={amount_usdt} USDT")
        attempts = [
            ("/bapi/futures/v1/private/future/spot-copy-trade/copy-portfolio/start-copy", {
                "leadPortfolioId": lead_portfolio_id,
                "investAmount": str(int(amount_usdt)),
                "profitSharingRatio": profit_share,
            }),
            ("/bapi/futures/v1/private/future/spot-copy-trade/copy-portfolio/create", {
                "leadPortfolioId": lead_portfolio_id,
                "investAmount": str(int(amount_usdt)),
                "profitSharingRatio": profit_share,
            }),
            ("/bapi/futures/v1/private/future/copy-trade/copy-portfolio/start-copy", {
                "leadPortfolioId": lead_portfolio_id,
                "copyAmount": str(int(amount_usdt)),
                "profitSharingRatio": profit_share,
            }),
        ]
        for path, body in attempts:
            try:
                data = self._post(path, body)
                code = data.get("code", "?") if isinstance(data, dict) else "?"
                if code == "000000":
                    print(f"  start_copy success: {json.dumps(data)[:300]}")
                    return data
                print(f"  {path} -> {code}: {data.get('message','') if isinstance(data, dict) else data}")
            except RuntimeError:
                raise
            except Exception as e:
                print(f"  {path} error: {e}")
        raise RuntimeError(
            "start_copy: all endpoints failed. "
            "Re-run capture_session.py, click Copy+Confirm to find the real endpoint."
        )

    def stop_copy(self, copy_portfolio_id: str) -> dict:
        """Stop copying a trader. copy_portfolio_id = YOUR copy portfolio ID."""
        print(f"  stop_copy: copyPortfolioId={copy_portfolio_id}")
        body = {"copyPortfolioId": copy_portfolio_id}
        for path in [
            "/bapi/futures/v1/private/future/spot-copy-trade/copy-portfolio/stop-copy",
            "/bapi/futures/v1/private/future/spot-copy-trade/copy-portfolio/close",
            "/bapi/futures/v1/private/future/copy-trade/copy-portfolio/stop-copy",
        ]:
            try:
                data = self._post(path, body)
                code = data.get("code", "?") if isinstance(data, dict) else "?"
                if code == "000000":
                    print(f"  stop_copy success: {json.dumps(data)[:300]}")
                    return data
                print(f"  {path} -> {code}: {data.get('message','') if isinstance(data, dict) else data}")
            except RuntimeError:
                raise
            except Exception as e:
                print(f"  {path} error: {e}")
        raise RuntimeError(
            "stop_copy: all endpoints failed. "
            "Re-run capture_session.py, click Stop Copy to find the real endpoint."
        )


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    with BinanceAPI() as api:
        if cmd == "status":
            ok = api.check_login()
            if ok:
                copies = api.list_copies()
                print(f"\nCurrently copying {len(copies)} trader(s):")
                for pid in copies:
                    print(f"  leadPortfolioId: {pid}")

        elif cmd == "start":
            if len(sys.argv) < 4:
                print("Usage: binance_api.py start <leadPortfolioId> <amount_usdt>")
                sys.exit(1)
            api.start_copy(sys.argv[2], float(sys.argv[3]))

        elif cmd == "stop":
            if len(sys.argv) < 3:
                print("Usage: binance_api.py stop <copyPortfolioId>")
                sys.exit(1)
            api.stop_copy(sys.argv[2])

        else:
            print(f"Unknown command: {cmd}")
            print("Commands: status, start, stop")
