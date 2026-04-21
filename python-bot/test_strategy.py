from smart_trader import SmartTrader
import pandas as pd


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
        'avg_entry': 100.0,
        'stop_loss': 98.5,
        'tp1_price': None,
        'take_profit': 101.0,
        'risk_percent': 0.03,
        'rr_target': 1.0,
        'entry_type': 'scout',
        'type': 'B+',
        'fallback_trade': False,
        'strong_trend': False,
        'atr_value': 2.0,
        'entry_reason': 'TREND SCOUT',
        'market_condition': 'trending',
        'entry_time': None,
        'entry_fee': 0.0,
        'entry_slippage': 0.0,
        'allocated_notional': 30.0,
        'target_position_notional': 100.0,
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

    expected_add_qty = round((100.0 - 30.0) / 105.0, 3)
    expected_total_qty = before_quantity + expected_add_qty
    expected_allocated_notional = 30.0 + (expected_add_qty * 105.0)
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
    assert abs(result['allocated_notional'] - expected_allocated_notional) < 1e-9
    assert abs(result['target_position_notional'] - 100.0) < 1e-9
    assert abs(result['entry_price'] - expected_avg_entry) < 1e-9
    assert result['entry_type'] == 'a_plus'
    assert abs(result['avg_entry'] - expected_avg_entry) < 1e-9
    assert result['type'] == 'A+'
    assert result['scaled_in'] is True
    assert result['scale_in_count'] == 1
    assert abs(result['stop_loss'] - expected_stop) < 1e-9
    assert result['stop_loss'] < result['entry_price']
    assert abs(result['tp1_price'] - expected_tp1) < 1e-9
    assert abs(result['take_profit'] - expected_tp2) < 1e-9


def test_position_scaling():
    scout_price = 100.0
    scout_notional = 30.0
    scale_price = 105.0
    scale_notional = 70.0

    scout_quantity = scout_notional / scout_price
    scale_quantity = scale_notional / scale_price
    total_quantity = scout_quantity + scale_quantity
    avg_entry = ((scout_price * scout_quantity) + (scale_price * scale_quantity)) / total_quantity

    assert abs((scout_quantity * scout_price) + (scale_quantity * scale_price) - 100.0) < 1e-9
    assert 100.0 < avg_entry < 105.0

def test_check_scale_in():
    trader = SmartTrader.__new__(SmartTrader)
    decision = trader.check_scale_in(
        {
            'avg_entry': 100.0,
        },
        {
            'close': 110.0,
            'resistance': 110.0,
            'volume': 120.0,
            'avg_volume': 100.0,
        }
    )
    blocked = trader.check_scale_in(
        {
            'avg_entry': 112.0,
        },
        {
            'close': 110.0,
            'resistance': 110.0,
            'volume': 120.0,
            'avg_volume': 100.0,
        }
    )

    assert decision == {'add_size': 0.7}
    assert blocked is False


def test_check_exit():
    trader = SmartTrader.__new__(SmartTrader)
    trader.time_exit_candles = 3

    a_plus_position = {'avg_entry': 100.0, 'entry_price': 100.0, 'type': 'A+', 'stop_loss': 98.5}
    b_plus_position = {'avg_entry': 100.0, 'entry_price': 100.0, 'type': 'B+', 'stop_loss': 98.5}
    losing_position = {'avg_entry': 100.0, 'entry_price': 100.0, 'type': 'B+', 'stop_loss': 98.5}
    hard_stop_position = {'avg_entry': 100.0, 'entry_price': 100.0, 'type': 'A+', 'stop_loss': 98.5}

    assert trader.check_exit(a_plus_position, {'close': 102.6}, 1) == 'EXIT'
    assert abs(a_plus_position['stop_loss'] - 100.0) < 1e-9
    assert trader.check_exit(b_plus_position, {'close': 101.3}, 1) == 'EXIT'
    assert trader.check_exit(losing_position, {'close': 99.9}, 3) == 'EXIT'
    assert trader.check_exit(hard_stop_position, {'close': 98.4}, 1) == 'EXIT'


def test_ema20_pullback_context():
    trader = SmartTrader.__new__(SmartTrader)
    df = pd.DataFrame({
        'low': [99.0, 100.0, 101.0, 101.4],
        'high': [100.5, 101.5, 102.5, 102.1],
        'open': [99.5, 100.5, 101.5, 101.8],
        'close': [100.0, 101.0, 102.0, 101.7],
        'volume': [100.0, 110.0, 120.0, 130.0],
    })
    bb = {
        'upper': 104.0,
        'middle': 100.5,
    }

    context = trader.get_trend_continuation_context(
        df=df,
        price=101.7,
        ema20=101.5,
        ema50=100.0,
        bb=bb,
    )

    assert bool(context['trend_up'])
    assert bool(trader.detect_higher_lows(df))
    assert bool(context['ema20_pullback_ready'])
    assert bool(context['continuation_ready'])

    built_context = trader.build_context({
        'df': df,
        'price': 101.7,
        'resistance': 104.0,
        'support': 99.0,
        'ema_fast': 101.9,
        'ema_slow': 101.1,
        'ema20': 101.5,
        'ema50': 100.0,
        'volume': 130.0,
        'avg_volume': 120.0,
        'atr': 1.5,
        'rsi': 58.0,
        'macd': {'macd': 1.2, 'signal': 1.0, 'prev_macd': 0.9},
        'bb': bb,
        'market_type': 'TRENDING',
    })

    assert bool(built_context['trend_aligned'])


def test_market_filter_skipped_for_core_pairs():
    trader = SmartTrader.__new__(SmartTrader)
    assert bool(trader.should_skip_market_filter('BTCUSDT'))
    assert bool(trader.should_skip_market_filter('ETHUSDT'))
    assert not trader.should_skip_market_filter('SOLUSDT')


def test_check_entry():
    trader = SmartTrader.__new__(SmartTrader)
    pullback = trader.check_entry('BTCUSDT', {
        'close': 100.2,
        'ema20': 100.0,
        'volume': 100.0,
        'avg_volume': 100.0,
        'resistance': 110.0,
        'trend_up': True,
        'mtf_bullish': 2,
        'compression': False,
        'higher_lows': True,
    }, 'BEARISH', None)
    breakout = trader.check_entry('BTCUSDT', {
        'close': 109.9,
        'ema20': 100.0,
        'volume': 120.0,
        'avg_volume': 100.0,
        'resistance': 110.0,
        'trend_up': False,
        'mtf_bullish': 2,
        'compression': False,
        'higher_lows': False,
    }, 'BEARISH', None)
    scout = trader.check_entry('ETHUSDT', {
        'close': 100.0,
        'ema20': 99.0,
        'volume': 100.0,
        'avg_volume': 100.0,
        'resistance': 110.0,
        'trend_up': False,
        'mtf_bullish': 2,
        'compression': True,
        'higher_lows': True,
    }, 'NEUTRAL', None)
    blocked_market = trader.check_entry('SOLUSDT', {
        'close': 100.2,
        'ema20': 100.0,
        'volume': 100.0,
        'avg_volume': 100.0,
        'resistance': 110.0,
        'trend_up': True,
        'mtf_bullish': 2,
        'compression': False,
        'higher_lows': True,
    }, 'NEUTRAL', None)
    trend_pullback_only = trader.check_entry('BTCUSDT', {
        'close': 100.2,
        'ema20': 100.0,
        'volume': 100.0,
        'avg_volume': 100.0,
        'resistance': 110.0,
        'trend_up': True,
        'mtf_bullish': 1,
        'compression': False,
        'higher_lows': True,
    }, 'BULLISH', None)
    relaxed_pullback = trader.check_entry('BTCUSDT', {
        'close': 100.2,
        'ema20': 100.0,
        'volume': 100.0,
        'avg_volume': 100.0,
        'resistance': 110.0,
        'trend_up': False,
        'mtf_bullish': 2,
        'compression': False,
        'higher_lows': False,
    }, 'BEARISH', None)

    assert pullback['entry_type'] == 'pullback'
    assert pullback['type'] == 'A+'
    assert breakout['entry_type'] == 'breakout'
    assert breakout['type'] == 'A+'
    assert relaxed_pullback['entry_type'] == 'pullback'
    assert relaxed_pullback['type'] == 'A+'
    assert trend_pullback_only['entry_type'] == 'pullback'
    assert trend_pullback_only['type'] == 'A+'
    assert scout['entry_type'] == 'scout'
    assert scout['type'] == 'B+'
    assert scout['size'] == 0.3
    assert blocked_market is None


class FakeAnalyzeMarketFilterTrader(SmartTrader):
    def __init__(self):
        self.daily_profit = 0.0
        self.daily_profit_target = 20.0
        self.symbol_state = {}
        self.open_positions = []
        self.enable_micro_b_plus_test = False
        self.only_a_plus_after_loss = False
        self.daily_losing_trades = 0
        self.min_adx_for_entry = 18
        self.adx_trend_threshold = 25
        self.min_expected_move_percent = 0.7
        self.near_level_percent = 1.5

    def get_candles(self, symbol, interval='15m', limit=100):
        rows = []
        for index in range(60):
            rows.append({
                'open': 100.0 + (index * 0.1),
                'high': 101.0 + (index * 0.1),
                'low': 99.0 + (index * 0.1),
                'close': 100.2 + (index * 0.1),
                'volume': 1000.0 + index,
            })
        return pd.DataFrame(rows)

    def calculate_rsi(self, closes, period=14):
        return 58.0

    def calculate_macd(self, closes):
        return {'macd': 1.2, 'signal': 1.0, 'histogram': 0.3, 'prev_histogram': 0.2, 'prev_macd': 0.9}

    def calculate_ema(self, closes, period):
        mapping = {7: 106.5, 18: 105.8, 20: 106.1, 50: 103.0}
        return mapping[period]

    def calculate_atr(self, df, period=14):
        return 2.0

    def calculate_adx(self, df, period=14):
        return {'adx': 30.0, 'plus_di': 25.0, 'minus_di': 15.0}

    def calculate_bollinger(self, closes, period=20, std_dev=2):
        return {'upper': 112.0, 'prev_upper': 111.5, 'middle': 104.0, 'lower': 96.0, 'pb': 0.65, 'width': 0.04}

    def bollinger_breakout_signal(self, df, bb):
        return {'breakout': False, 'strong_breakout': False, 'volume_ratio': 1.0}

    def get_volume_ratio(self, df):
        return 1.05

    def session_filter(self):
        return True

    def calculate_support_resistance(self, df):
        return {'support': 100.0, 'resistance': 130.0, 'range': 30.0, 'mid_point': 115.0}

    def calculate_levels(self, df, lookback=20):
        return 130.0, 100.0

    def build_context(self, data):
        return {
            'scout': False,
            'score': 4,
            'rising_volume': True,
            'breakout': False,
            'continuation_ready': True,
            'market': 'TRENDING',
            'trend_up': True,
            'soft_pullback': False,
            'ema20_pullback_ready': True,
            'upper_band_ride': False,
        }

    def classify_setup_quality(self, data, context):
        return 'A+'

    def is_strong_breakout(self, data, context):
        return False

    def has_confirmation_candle(self, df, direction='bullish'):
        return True

    def get_multi_timeframe_count(self, symbol):
        return 2 if symbol == 'BTCUSDT' else 1

    def btc_is_healthy(self):
        return False

    def get_btc_bias(self):
        return 'BEARISH'

    def valid_breakout_setup(self, price, rsi, macd_val, signal_val, prev_macd, ema):
        return True


def test_analyze_market_filter_skip_for_core_pairs():
    trader = FakeAnalyzeMarketFilterTrader()

    btc_signal = trader.analyze('BTCUSDT')
    sol_signal = trader.analyze('SOLUSDT')

    assert btc_signal['action'] == 'BUY'
    assert 'EMA20 pullback' in btc_signal['reason']
    assert sol_signal['action'] == 'HOLD'
    assert sol_signal['reason'] == 'No valid setup (4/5 checks)'


class FakeAnalyzeEntryScoreTrader(FakeAnalyzeMarketFilterTrader):
    def btc_is_healthy(self):
        return True


def test_analyze_entry_confirmation_score_gate():
    trader = FakeAnalyzeEntryScoreTrader()

    btc_signal = trader.analyze('BTCUSDT')
    sol_signal = trader.analyze('SOLUSDT')

    assert btc_signal['action'] == 'BUY'
    assert btc_signal['entry_tier'] == 'A+'
    assert sol_signal['action'] == 'HOLD'
    assert sol_signal['reason'] == 'No valid setup (4/5 checks)'


if __name__ == '__main__':
    test_scout_scale_merge_math()
    test_position_scaling()
    test_check_scale_in()
    test_check_exit()
    test_ema20_pullback_context()
    test_market_filter_skipped_for_core_pairs()
    test_check_entry()
    test_analyze_market_filter_skip_for_core_pairs()
    test_analyze_entry_confirmation_score_gate()

    trader = SmartTrader()
    symbols = ['ETHUSDT', 'BTCUSDT']

    for symbol in symbols:
        signal = trader.analyze(symbol)
        print_signal(symbol, signal)
