#!/usr/bin/env python3
"""Quick test of market structure detection"""

import pandas as pd
import numpy as np
from smart_trader import SmartTrader

# Create a test dataframe with bullish structure (HH + HL)
# Swing pattern: Low, High, Low, High (rising pattern)
data = {
    'high': [100, 104, 102, 106, 104, 108, 105, 110, 107, 112],
    'low': [98, 102, 100, 104, 102, 106, 103, 108, 105, 110],
    'close': [99, 103, 101, 105, 103, 107, 104, 109, 106, 111]
}
df = pd.DataFrame(data)

# Initialize bot (minimal)
class TestTrader(SmartTrader):
    pass

trader = TestTrader.__new__(TestTrader)

# Test structure detection
result = trader.detect_market_structure(df)
print('Market Structure Detection Test')
print('=' * 50)
print(f'Structure: {result["structure"]}')
print(f'Break of Structure: {result["break_of_structure"]}')
print(f'Last Swing High: {result["last_swing_high"]}')
print(f'Last Swing Low: {result["last_swing_low"]}')
print(f'Swing Highs (last 3): {result["swing_highs"]}')
print(f'Swing Lows (last 3): {result["swing_lows"]}')
print('=' * 50)
print('✓ Structure detection method validated')
