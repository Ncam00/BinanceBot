from smart_trader import SmartTrader


def print_signal(symbol, signal):
    print(f"\n=== {symbol} ===")
    print(f"Action:      {signal.get('action', 'N/A')}")
    print(f"Reason:      {signal.get('reason', 'N/A')}")
    print(f"Strength:    {signal.get('strength', 'N/A')}")
    print(f"Market type: {signal.get('market_type', 'N/A')}")
    print(f"Zone:        {signal.get('zone', 'N/A')}")
    print(f"Price:       {signal.get('price', 'N/A')}")
    print(f"Support:     {signal.get('support', 'N/A')}")
    print(f"Resistance:  {signal.get('resistance', 'N/A')}")
    if signal.get('entry_type'):
        print(f"Entry type:  {signal.get('entry_type')}")
    if signal.get('support_override'):
        print(f"Override:    {signal.get('support_override')}")


if __name__ == '__main__':
    trader = SmartTrader()
    symbols = ['ETHUSDT', 'BTCUSDT', 'SOLUSDT']

    for symbol in symbols:
        signal = trader.analyze(symbol)
        print_signal(symbol, signal)
