import sys
import os
sys.path.insert(0, r"C:\BinanceBot")

# One-shot test - fetch traders and print results then exit
import copy_monitor_new as m

try:
    traders = m.fetch_traders()
    print(f"Got {len(traders)} traders")
    if traders:
        # find x1Boost
        xb = next((t for t in traders if "x1boost" in (t.get("nickname", "") or "").lower()), None)
        if xb:
            p = m.parse_trader(xb)
            print(f"x1Boost: {p['copiers']}/{p['max_copiers']} copiers, {p['slots_free']} slot(s) free, ROI {p['roi_30']:.1f}%")
        else:
            print("x1Boost not in first 50 traders (sorted by ROI)")
        top = sorted([m.parse_trader(t) for t in traders], key=m.score, reverse=True)[:5]
        print("Top 5 by score:")
        for p in top:
            print(f"  {p['name']} score={m.score(p)} ROI={p['roi_30']:.1f}% MDD={p['mdd']:.1f}% days={p['days']} slots={p['slots_free']}")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    if m._browser:
        try:
            m._browser.close()
        except Exception:
            pass
    if m._pw:
        try:
            m._pw.stop()
        except Exception:
            pass
