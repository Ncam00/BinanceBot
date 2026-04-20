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


class FakeScoutScaleTrader(SmartTrader):
    def __init__(self):
        self.trade_lock = False
        self.open_positions = []
        self.daily_trades = 0
        self.last_trade_time = None
        self.primary_candle_minutes = 15
        self.time_exit_candles = 4
        self.max_position_cap = 0.25
        self.atr_stop_multiplier = 1.5
        self.last_trade_win = False
        self.sent_messages = []
        self.mock_balance = 1000.0
        self.mock_price = 105.0
        self.mock_step_size = 0.001
        self.mock_precision = 3
        self.mock_fill_price = 105.0

    def get_balance(self):
        return self.mock_balance

    def get_symbol_precision(self, symbol):
        return self.mock_step_size, self.mock_precision

    def calculate_order_fee_usdt(self, order, symbol, fallback_price=None):
        return 0.0

    def send_telegram(self, message):
        self.sent_messages.append(message)

    def dynamic_tp_sl(self, entry_price, score, signal):
        profile = {
            'entry_type': 'a_plus',
            'balance_fraction': 0.10,
            'tp_mode': 'runner_percent',
            'tp1_percent': 2.5,
            'tp2_percent': 4.0,
            'sl_atr_multiplier': 1.5,
        }
        return entry_price * 1.025, entry_price * 1.04, entry_price * 0.985, profile

    def get_score_position_size(self, balance, score, signal):
        return 100.0

    @property
    def client(self):
        trader = self

        class MockClient:
            def create_order(self, symbol, side, type, quantity):
                return {
                    'fills': [
                        {
                            'price': f"{trader.mock_fill_price:.4f}",
                            'commission': '0',
                            'commissionAsset': 'USDT',
                        }
                    ]
                }

        return MockClient()


def test_scout_scale_merge_math():
    trader = FakeScoutScaleTrader()
    scout_position = {
        'trade_id': 'BTCUSDT-1',
        'symbol': 'BTCUSDT',
        'quantity': 0.3,
        'original_quantity': 0.3,
        'entry_price': 100.0,
        'stop_loss': 98.5,
        'tp1_price': None,
        'take_profit': 101.0,
        'risk_percent': 0.03,
        'rr_target': 1.0,
        'entry_type': 'scout',
        'fallback_trade': False,
        'strong_trend': False,
        'atr_value': 2.0,
        'entry_reason': 'TREND SCOUT',
        'market_condition': 'trending',
        'entry_time': None,
        'entry_fee': 0.0,
        'entry_slippage': 0.0,
        'realized_pnl': 0.0,
        'partial_taken': False,
        'runner_active': False,
        'be_active': False,
        'trailing_stop_active': False,
        'highest_price': 100.0,
        'trailing_stop_price': None,
        'scaled_in': False,
        'scale_in_count': 0,
        'scale_in_reason': None,
        'signal': {'trend_scout': True},
    }
    signal = {
        'action': 'BUY',
        'price': 105.0,
        'score': 4,
        'entry_tier': 'A+',
        'breakout': True,
        'early_breakout': False,
        'strong_trend': True,
        'atr_value': 2.0,
        'market_type': 'TRENDING',
        'support': 102.0,
        'reason': 'Breakout confirmation after scout',
    }

    before_quantity = scout_position['quantity']
    result = trader.execute_scale_in(scout_position, signal)

    expected_add_qty = round((100.0 - (before_quantity * 105.0)) / 105.0, 3)
    expected_total_qty = before_quantity + expected_add_qty
    expected_avg_entry = ((100.0 * before_quantity) + (105.0 * expected_add_qty)) / expected_total_qty
    expected_stop = max(expected_avg_entry - (2.0 * 1.5), 102.0 * 0.995, expected_avg_entry * 0.97)
    expected_tp1 = expected_avg_entry * 1.025
    expected_tp2 = expected_avg_entry * 1.04

    print("\n=== Scout Scale Test ===")
    print(f"Added quantity: {expected_add_qty:.3f}")
    print(f"Total quantity: {result['quantity']:.3f}")
    print(f"Average entry:  {result['entry_price']:.4f}")
    print(f"Stop loss:      {result['stop_loss']:.4f}")
    print(f"Take profit:    {result['take_profit']:.4f}")

    assert result is scout_position
    assert abs(result['quantity'] - expected_total_qty) < 1e-9
    assert abs(result['original_quantity'] - expected_total_qty) < 1e-9
    assert abs(result['entry_price'] - expected_avg_entry) < 1e-9
    assert result['entry_type'] == 'a_plus'
    assert result['scaled_in'] is True
    assert result['scale_in_count'] == 1
    assert abs(result['stop_loss'] - expected_stop) < 1e-9
    assert result['stop_loss'] < result['entry_price']
    assert abs(result['tp1_price'] - expected_tp1) < 1e-9
    assert abs(result['take_profit'] - expected_tp2) < 1e-9


if __name__ == '__main__':
    test_scout_scale_merge_math()

    trader = SmartTrader()
    symbols = ['ETHUSDT', 'BTCUSDT']

    for symbol in symbols:
        signal = trader.analyze(symbol)
        print_signal(symbol, signal)
