#!/usr/bin/env python3
"""Test volume profile POC analysis"""

import pandas as pd
import numpy as np
from smart_trader import SmartTrader

# Create test dataframe with volume clustering at specific price levels
data = {
    'high': [100, 101, 99, 101.5, 100.5, 101, 99.5, 102, 100.5, 101.2],
    'low': [99, 100, 98, 100.5, 99.5, 100, 98.5, 101, 99.5, 100.2],
    'close': [99.5, 100.5, 98.5, 101, 100, 100.5, 99, 101.5, 100, 100.8],
    'volume': [1000, 1500, 800, 2000, 1200, 1800, 900, 2500, 1400, 1100]  # High at 101-102
}
df = pd.DataFrame(data)

# Initialize bot (minimal)
class TestTrader(SmartTrader):
    pass

trader = TestTrader.__new__(TestTrader)

# Test POC detection
poc_price, vah, val = trader.get_volume_poc(df, lookback=10, buckets=20)
print('Volume Profile Analysis Test')
print('=' * 60)
print(f'Point of Control (POC): ${poc_price:.4f}' if poc_price else 'POC: None')
print(f'Value Area High (VAH):  ${vah:.4f}' if vah else 'VAH: None')
print(f'Value Area Low (VAL):   ${val:.4f}' if val else 'VAL: None')
print(f'Current Price:          ${df["close"].iloc[-1]:.4f}')
if poc_price:
    print(f'Distance to POC:        {abs(df["close"].iloc[-1] - poc_price) / df["close"].iloc[-1] * 100:.3f}%')
print('=' * 60)
if poc_price and val and vah:
    print('✓ Volume POC method validated')
else:
    print('✗ Volume POC calculation incomplete')
