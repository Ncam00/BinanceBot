"""Quick structural tests for the 4 strategic improvements.
Does NOT hit Binance — only validates code structure + window math."""
import inspect
from datetime import datetime
import smart_trader_v3_live as m

cls = m.SmartTrader
failures = []

def check(cond, msg):
    if cond:
        print(f"  PASS: {msg}")
    else:
        print(f"  FAIL: {msg}")
        failures.append(msg)

print("\n=== 1. htf_trend_bullish helper ===")
check(hasattr(cls, "htf_trend_bullish"), "method exists")
if hasattr(cls, "htf_trend_bullish"):
    sig = inspect.signature(cls.htf_trend_bullish)
    check(len(sig.parameters) == 2, f"signature has 2 params (self, symbol): {sig}")
    src = inspect.getsource(cls.htf_trend_bullish)
    check("'1h'" in src, "uses 1h timeframe")
    check("ema20" in src.lower() and "ema50" in src.lower(), "uses EMA20/EMA50")

print("\n=== 2. check_entry wires all 4 improvements ===")
src_ce = inspect.getsource(cls.check_entry)
check("in_london" in src_ce and "in_us" in src_ce, "session window vars present")
check("utc_time" in src_ce, "UTC hour calculation present")
check("htf_trend_bullish" in src_ce, "1h filter called")
check("'atr': top_atr" in src_ce, "ATR passed in signal for ATR-based TPs")
check("'position_boost'" in src_ce, "conviction sizing passed in signal")
check("conviction_size" in src_ce, "conviction_size variable used")

print("\n=== 3. execute_buy consumes position_boost ===")
src_eb = inspect.getsource(cls.execute_buy)
check("position_boost = signal.get('position_boost'" in src_eb,
      "execute_buy reads signal.position_boost")
check("usdt_target *= position_boost" in src_eb, "size multiplied by boost")

print("\n=== 4. ATR-based TP wiring in execute_buy ===")
check("_atr_for_targets = signal.get('atr'" in src_eb, "ATR pulled from signal")
check("_atr_for_targets * TP1_MULTIPLIER" in src_eb, "TP1 uses ATR * TP1_MULTIPLIER")
check("_atr_for_targets * tp_multiplier" in src_eb, "Runner TP uses ATR multiplier")

print("\n=== 5. Session window — current UTC ===")
utc = datetime.utcnow()
utc_time = utc.hour + utc.minute / 60.0
in_london = 7.0 <= utc_time < 11.0
in_us = 13.0 <= utc_time < 17.0
will_trade = in_london or in_us
print(f"  UTC now: {utc.strftime('%H:%M')}  in_london={in_london}  in_us={in_us}  -> entries_allowed={will_trade}")

print("\n=== 6. Module imports cleanly ===")
check(hasattr(m, "TP1_MULTIPLIER"), "TP1_MULTIPLIER constant exists")
check(hasattr(m, "RUNNER_MULTIPLIER"), "RUNNER_MULTIPLIER constant exists")
check(hasattr(m, "MAX_TRADES_PER_DAY"), "MAX_TRADES_PER_DAY constant exists")

print("\n" + "=" * 50)
if failures:
    print(f"FAILED: {len(failures)} checks")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL TESTS PASSED")
