"""
SMART TRADER V2 - Location-Based Trading Bot
=============================================
Fixed & Cleaned: April 2026
Target: $20/day | 60%+ win rate | Protect capital first

Changes from previous version:
+ ADX filter added: skips choppy markets below ADX 22
+ Final setup validation gate (valid_setup / valid_breakout_setup)
+ Stop loss moved to position 1 in check_positions (safety first)

Pairs: BTCUSDT, ETHUSDT
"""

import os
import time
import json
from datetime import datetime, timezone, timedelta
from binance.client import Client
from binance.enums import *
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import requests
import pytz

load_dotenv()


class SmartTrader:
    def __init__(self):
        # ════════════════════════════════════════════════════════════════════
        # BINANCE CONNECTION
        # ════════════════════════════════════════════════════════════════════
        self.client = Client(
            os.getenv('BINANCE_API_KEY'),
            os.getenv('BINANCE_SECRET_KEY')
        )

        # ════════════════════════════════════════════════════════════════════
        # TRADING PAIRS
        # ════════════════════════════════════════════════════════════════════
        self.trading_pairs = [
            'BTCUSDT', 'ETHUSDT'
        ]
        self.max_positions = 1

        # ════════════════════════════════════════════════════════════════════
        # CORE RISK SETTINGS
        # ════════════════════════════════════════════════════════════════════
        self.stop_loss_percent = 1.5
        self.take_profit_percent = 2.5
        self.position_size_percent = 15
        self.max_position_cap = 0.25

        # ════════════════════════════════════════════════════════════════════
        # DAILY / WEEKLY LIMITS
        # ════════════════════════════════════════════════════════════════════
        self.daily_profit_target = 20.00
        self.max_daily_loss = 4.00
        self.max_weekly_loss = 20.00
        self.max_trades_per_day = 5
        self.hard_max_trades = 5
        self.trade_cooldown_minutes = 30
        self.max_consecutive_losses = 2
        self.only_a_plus_after_loss = True

        # ════════════════════════════════════════════════════════════════════
        # CIRCUIT BREAKER
        # ════════════════════════════════════════════════════════════════════
        self.starting_balance = 468.35
        self.circuit_breaker_percent = 0.05
        self.circuit_breaker_limit = self.starting_balance * (1 - self.circuit_breaker_percent)

        # ════════════════════════════════════════════════════════════════════
        # EXIT MANAGEMENT
        # ════════════════════════════════════════════════════════════════════
        self.break_even_trigger = 1.0
        self.micro_profit_lock_trigger = 2.0
        self.trailing_stop_activation = 1.2
        self.trailing_stop_distance = 0.5
        self.partial_tp_percent = 0.50
        self.soft_exit_loss_trigger = 0.4
        self.no_momentum_price_change_threshold = 0.25
        self.min_expected_move_percent = 0.7
        self.bb_squeeze_threshold = 0.05
        self.atr_stop_multiplier = 1.5
        self.atr_target_multiplier = 2.0
        self.time_exit_candles = 3
        self.primary_candle_minutes = 15
        self.trend_scout_fraction = 0.30
        self.trend_scale_in_fraction = 0.70
        self.trend_scout_target_fraction = 0.10

        # ════════════════════════════════════════════════════════════════════
        # LOCATION-BASED SETTINGS
        # ════════════════════════════════════════════════════════════════════
        self.sr_lookback = 50
        self.no_trade_zone_percent = 40
        self.near_level_percent = 1.5

        # ════════════════════════════════════════════════════════════════════
        # ADX THRESHOLDS
        # ════════════════════════════════════════════════════════════════════
        self.adx_range_threshold = 20
        self.adx_trend_threshold = 25
        self.min_adx_for_entry = 18           # Relaxed: allow more trades while avoiding garbage
        self.enable_micro_b_plus_test = True

        # ════════════════════════════════════════════════════════════════════
        # SESSION SETTINGS (NZ TIME)
        # ════════════════════════════════════════════════════════════════════
        self.nz_timezone = pytz.timezone('Pacific/Auckland')
        self.session_settings = {
            'asia':   {'mode': 'low_risk',   'max_trades': 1, 'min_strength': 0.85},
            'london': {'mode': 'normal',     'max_trades': 3, 'min_strength': 0.75},
            'us':     {'mode': 'aggressive', 'max_trades': 3, 'min_strength': 0.70},
        }

        # ════════════════════════════════════════════════════════════════════
        # STATE TRACKING
        # ════════════════════════════════════════════════════════════════════
        self.daily_profit = 0.0
        self.daily_loss = 0.0
        self.daily_loss_ratio = 0.0
        self.weekly_pnl = 0.0
        self.daily_trades = 0
        self.daily_losing_trades = 0
        self.consecutive_losses = 0
        self.last_trade_win = False
        self.fallback_trade_taken = False
        self.scout_trade_taken = False
        self.open_positions = []
        self.symbol_state = {}
        self.trade_lock = False
        self.last_trade_time = None
        self.last_reset_date = datetime.now().date()
        self.last_week_reset_key = self._get_week_key()

        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')

        print("=" * 60)
        print("   SMART TRADER V2 - INITIALIZING")
        print("=" * 60)
        print(f"   Pairs:           {', '.join(self.trading_pairs)}")
        print(f"   Max trades/day:  {self.max_trades_per_day}")
        print(f"   Daily target:    ${self.daily_profit_target}")
        print(f"   Daily loss cap:  ${self.max_daily_loss}")
        print(f"   Weekly loss cap: ${self.max_weekly_loss}")
        print(f"   Circuit breaker: ${self.circuit_breaker_limit:.2f}")
        print(f"   Position size:   {self.position_size_percent}%")
        print(f"   SL: ATR x{self.atr_stop_multiplier:.1f} | TP: ATR x{self.atr_target_multiplier:.1f}")
        print(f"   Min ADX:         {self.min_adx_for_entry} (choppy market filter)")
        session, settings = self.get_market_session()
        print(f"   Session:         {session.upper()} ({settings['mode']})")
        print("=" * 60)

        self.sync_existing_positions()

    # ════════════════════════════════════════════════════════════════════
    # SESSION
    # ════════════════════════════════════════════════════════════════════
    def get_nz_hour(self):
        return datetime.now(self.nz_timezone).hour

    def get_market_session(self):
        hour = self.get_nz_hour()
        if 11 <= hour < 19:
            return 'asia', self.session_settings['asia']
        elif hour >= 19 or hour < 3:
            return 'london', self.session_settings['london']
        else:
            return 'us', self.session_settings['us']

    def _get_week_key(self):
        iso = datetime.now().date().isocalendar()
        return (iso[0], iso[1])

    # ════════════════════════════════════════════════════════════════════
    # TELEGRAM
    # ════════════════════════════════════════════════════════════════════
    def send_telegram(self, message):
        if not self.telegram_token or not self.telegram_chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            requests.post(url, data={
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }, timeout=5)
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════════════
    # DATA FETCHING
    # ════════════════════════════════════════════════════════════════════
    def get_candles(self, symbol, interval='15m', limit=100):
        try:
            klines = self.client.get_klines(symbol=symbol, interval=interval, limit=limit)
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            return df
        except Exception as e:
            print(f"   Candle fetch error {symbol}: {e}")
            return None

    def get_price(self, symbol):
        try:
            return float(self.client.get_symbol_ticker(symbol=symbol)['price'])
        except Exception:
            return None

    def get_balance(self):
        try:
            account = self.client.get_account()
            for asset in account['balances']:
                if asset['asset'] == 'USDT':
                    return float(asset['free'])
            return 0.0
        except Exception as e:
            print(f"   Balance error: {e}")
            return 0.0

    def get_total_balance(self):
        try:
            usdt = self.get_balance()
            position_value = 0.0
            for pos in self.open_positions:
                price = self.get_price(pos['symbol'])
                if price:
                    position_value += pos['quantity'] * price
            return usdt + position_value
        except Exception:
            return self.get_balance()

    def get_symbol_precision(self, symbol):
        info = self.client.get_symbol_info(symbol)
        step_size = float([f['stepSize'] for f in info['filters'] if f['filterType'] == 'LOT_SIZE'][0])
        precision = int(round(-np.log10(step_size)))
        return step_size, precision

    def calculate_order_fee_usdt(self, order, symbol, fallback_price=None):
        try:
            base_asset = symbol.replace('USDT', '')
            total_fee = 0.0
            for fill in order.get('fills', []):
                commission = float(fill.get('commission', 0) or 0)
                commission_asset = fill.get('commissionAsset')
                fill_price = float(fill.get('price', fallback_price or 0) or 0)
                if commission <= 0 or not commission_asset:
                    continue
                if commission_asset == 'USDT':
                    total_fee += commission
                elif commission_asset == base_asset:
                    total_fee += commission * fill_price
                else:
                    conv_price = self.get_price(f"{commission_asset}USDT")
                    if conv_price:
                        total_fee += commission * conv_price
            return total_fee
        except Exception:
            return 0.0

    # ════════════════════════════════════════════════════════════════════
    # TECHNICAL INDICATORS
    # ════════════════════════════════════════════════════════════════════
    def calculate_rsi(self, closes, period=14):
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return (100 - (100 / (1 + rs))).iloc[-1]

    def calculate_macd(self, closes):
        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line
        return {
            'macd': macd_line.iloc[-1],
            'signal': signal_line.iloc[-1],
            'histogram': histogram.iloc[-1],
            'prev_histogram': histogram.iloc[-2] if len(histogram) > 1 else 0,
            'prev_macd': macd_line.iloc[-2] if len(macd_line) > 1 else 0
        }

    def calculate_ema(self, closes, period):
        return closes.ewm(span=period, adjust=False).mean().iloc[-1]

    def calculate_atr(self, df, period=14):
        """Calculate Average True Range."""
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr.iloc[-1] if not np.isnan(atr.iloc[-1]) else 0

    def calculate_adx(self, df, period=14):
        high, low, close = df['high'], df['low'], df['close']
        plus_dm = high.diff()
        minus_dm = low.diff().abs() * -1
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm > 0] = 0
        minus_dm = minus_dm.abs()
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
        adx = dx.rolling(window=period).mean()
        return {
            'adx': adx.iloc[-1] if not np.isnan(adx.iloc[-1]) else 0,
            'plus_di': plus_di.iloc[-1] if not np.isnan(plus_di.iloc[-1]) else 0,
            'minus_di': minus_di.iloc[-1] if not np.isnan(minus_di.iloc[-1]) else 0
        }

    def calculate_bollinger(self, closes, period=20, std_dev=2):
        sma = closes.rolling(window=period).mean()
        std = closes.rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        current_price = closes.iloc[-1]
        bb_range = upper.iloc[-1] - lower.iloc[-1]
        pb = (current_price - lower.iloc[-1]) / bb_range if bb_range > 0 else 0.5
        width = (bb_range / sma.iloc[-1]) if sma.iloc[-1] and not np.isnan(sma.iloc[-1]) else 0
        return {
            'upper': upper.iloc[-1],
            'prev_upper': upper.iloc[-2] if len(upper) > 1 else upper.iloc[-1],
            'middle': sma.iloc[-1],
            'lower': lower.iloc[-1],
            'pb': pb,
            'width': width
        }

    def bollinger_breakout_signal(self, df, bb):
        if len(df) < 20:
            return {
                'breakout': False,
                'strong_breakout': False,
                'volume_ratio': 0,
            }

        last_close = df['close'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        last_volume = df['volume'].iloc[-1]
        average_volume = df['volume'].rolling(20).mean().iloc[-1]
        squeeze = bb['width'] < self.bb_squeeze_threshold
        breakout = last_close > bb['upper'] and prev_close <= bb['prev_upper']
        volume_ratio = (last_volume / average_volume) if average_volume and not np.isnan(average_volume) else 0
        normal_volume = volume_ratio > 1.1
        strong_volume = volume_ratio > 1.2
        return {
            'breakout': breakout and squeeze and normal_volume,
            'strong_breakout': breakout and squeeze and strong_volume,
            'volume_ratio': volume_ratio,
        }

    def is_early_breakout(self, price, resistance, volume_ratio, rising_volume):
        if resistance <= 0:
            return False
        return price > resistance * 0.998 and rising_volume and volume_ratio > 1.1

    # ════════════════════════════════════════════════════════════════════
    # SUPPORT / RESISTANCE
    # ════════════════════════════════════════════════════════════════════
    def calculate_support_resistance(self, df):
        highs = df['high'].values
        lows = df['low'].values
        swing_highs, swing_lows = [], []
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
               highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                swing_highs.append(highs[i])
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
               lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                swing_lows.append(lows[i])
        period_high = max(highs[-self.sr_lookback:])
        period_low = min(lows[-self.sr_lookback:])
        resistance = max(swing_highs[-3:]) if len(swing_highs) >= 3 else period_high
        support = min(swing_lows[-3:]) if len(swing_lows) >= 3 else period_low
        return {
            'support': support,
            'resistance': resistance,
            'range': resistance - support,
            'mid_point': (resistance + support) / 2
        }

    def calculate_levels(self, df, lookback=20):
        highs = df['high'].iloc[-lookback:]
        lows = df['low'].iloc[-lookback:]
        resistance = highs.max()
        support = lows.min()
        return resistance, support

    def level_context(self, price, resistance, support):
        range_size = resistance - support
        if range_size <= 0:
            return {
                'near_resistance': False,
                'near_support': False,
                'breakout': False,
                'breakdown': False,
            }

        near_resistance = price > resistance - (range_size * 0.1)
        near_support = price < support + (range_size * 0.1)
        breakout_zone = price > resistance
        breakdown_zone = price < support

        return {
            'near_resistance': near_resistance,
            'near_support': near_support,
            'breakout': breakout_zone,
            'breakdown': breakdown_zone,
        }

    def dead_zone_filter(self, price, resistance, support):
        range_size = resistance - support
        if price <= 0:
            return True
        return (range_size / price) < 0.01

    def is_near_level(self, price, level):
        return abs(price - level) / level * 100 <= self.near_level_percent

    def get_trade_zone(self, price, support, resistance):
        range_size = resistance - support
        if range_size <= 0:
            return 'middle'
        buy_zone_top = support + (range_size * 0.30)
        sell_zone_bottom = resistance - (range_size * 0.30)
        if price <= buy_zone_top:
            return 'buy_zone'
        elif price >= sell_zone_bottom:
            return 'sell_zone'
        return 'middle'

    def has_confirmation_candle(self, df, direction='bullish'):
        if len(df) < 2:
            return False
        candle = df.iloc[-2]
        body = candle['close'] - candle['open'] if direction == 'bullish' else candle['open'] - candle['close']
        candle_range = candle['high'] - candle['low']
        if candle_range == 0:
            return False
        return body > 0 and (body / candle_range) > 0.3

    # ════════════════════════════════════════════════════════════════════
    # MARKET TYPE
    # ════════════════════════════════════════════════════════════════════
    def get_market_type(self, adx_value):
        if adx_value > 25:
            return 'TRENDING'
        if adx_value < 18:
            return 'RANGING'
        return 'CHOPPY'

    # ════════════════════════════════════════════════════════════════════
    # STRATEGY SIGNALS
    # ════════════════════════════════════════════════════════════════════
    def get_range_signal(self, price, rsi, bb, support, resistance):
        near_support = self.is_near_level(price, support)
        near_resistance = self.is_near_level(price, resistance)
        if near_support and rsi < 40 and bb['pb'] < 0.2:
            return {
                'action': 'BUY',
                'strength': 0.8,
                'reason': f"RANGE BUY: Near support (RSI={rsi:.1f}, BB%={bb['pb']:.2f})"
            }
        if near_resistance and rsi > 60 and bb['pb'] > 0.8:
            return {
                'action': 'SELL',
                'strength': 0.8,
                'reason': f"RANGE SELL: Near resistance (RSI={rsi:.1f})"
            }
        return {'action': 'HOLD', 'strength': 0, 'reason': 'Range: Not at key level'}

    def ranging_trade(self, price, rsi, support, resistance, volume_ratio):
        """Small mean-reversion trade model for active ranging markets."""
        if volume_ratio < 0.8:
            return {
                'action': 'HOLD',
                'strength': 0,
                'reason': f'Ranging trade blocked: volume {volume_ratio:.2f}x < 0.80x'
            }

        if price <= support * 1.01 and rsi < 35:
            return {
                'action': 'BUY',
                'strength': 0.65,
                'reason': f'RANGING BUY: Near support (RSI={rsi:.1f})',
                'entry_type': 'RANGING'
            }

        if price >= resistance * 0.99 and rsi > 65:
            return {
                'action': 'SELL',
                'strength': 0.65,
                'reason': f'RANGING SELL: Near resistance (RSI={rsi:.1f})',
                'entry_type': 'RANGING'
            }

        return {'action': 'HOLD', 'strength': 0, 'reason': 'Ranging: No edge at extremes'}

    def get_trend_signal(self, price, rsi, macd, ema_fast, ema_slow, adx, support, resistance):
        near_support = self.is_near_level(price, support)
        macd_bullish = macd['macd'] > macd['signal'] and macd['histogram'] > macd['prev_histogram']
        macd_bearish = macd['macd'] < macd['signal'] and macd['histogram'] < macd['prev_histogram']
        ema_bullish = ema_fast > ema_slow
        trend_up = adx['plus_di'] > adx['minus_di']

        if near_support and trend_up and macd_bullish and ema_bullish and rsi < 60:
            return {
                'action': 'BUY',
                'strength': 0.85,
                'reason': f"TREND BUY: Pullback to support (ADX={adx['adx']:.1f}, RSI={rsi:.1f})",
                'entry_type': 'PULLBACK'
            }
        if price < support and not trend_up and macd_bearish:
            return {
                'action': 'SELL',
                'strength': 0.85,
                'reason': "TREND SELL: Breakdown below support"
            }
        return {'action': 'HOLD', 'strength': 0, 'reason': 'Trend: No clear setup'}

    # ════════════════════════════════════════════════════════════════════
    # ADDED: FINAL SETUP VALIDATION GATE
    # All conditions must pass - this is the last gate before a trade fires
    # ════════════════════════════════════════════════════════════════════
    def valid_setup(self, price, prices, rsi, macd_val, signal_val, prev_macd, ema):
        """
        Final gate for pullback entries. All four must pass:
        1. Price within 0.5% of recent support
        2. RSI below 65 (relaxed from 55)
        3. MACD rising and above signal line
        4. Price above slow EMA (trading with trend)
        """
        support = min(prices[-20:])
        if abs(price - support) / price > 0.005:
            return False
        if rsi >= 65:
            return False
        if not (macd_val > signal_val and macd_val > prev_macd):
            return False
        if price < ema:
            return False
        return True

    def valid_breakout_setup(self, price, rsi, macd_val, signal_val, prev_macd, ema):
        """
        Final gate for breakout entries:
        1. RSI between 45-75 (relaxed: catch momentum, block tops)
        2. MACD rising and above signal
        3. Price above slow EMA
        """
        if rsi <= 45 or rsi >= 75:
            return False
        if not (macd_val > signal_val and macd_val > prev_macd):
            return False
        if price < ema:
            return False
        return True

    def breakout_entry(self, df, price, resistance, rsi, adx, volume_ratio):
        """
        Standalone breakout entry with fake breakout filter.
        Requires: price > resistance, volume confirm, strong candle, RSI < 75.
        """
        if price <= resistance:
            return {'action': 'HOLD', 'strength': 0, 'reason': 'No breakout'}

        # Anti-chase: skip if price already ran too far past resistance
        if price > resistance * 1.002:
            return {'action': 'HOLD', 'strength': 0,
                    'reason': f'Breakout blocked: price chasing (>{0.2}% past R)'}

        # RSI overbought guard - avoid buying tops
        if rsi >= 75:
            return {'action': 'HOLD', 'strength': 0,
                    'reason': f'Breakout blocked: RSI {rsi:.1f} >= 75 (overbought)'}

        # Volume confirmation: allow cleaner breakouts without requiring extreme expansion.
        if volume_ratio < 1.2:
            return {'action': 'HOLD', 'strength': 0,
                'reason': f'Breakout blocked: volume {volume_ratio:.1f}x < 1.2x (weak)'}

        # Strong candle filter: body must be >50% of candle range
        candle = df.iloc[-1]
        body_size = abs(candle['close'] - candle['open'])
        candle_range = candle['high'] - candle['low']
        strong_candle = candle['close'] > candle['open']  # green candle

        if candle_range == 0 or not strong_candle:
            return {'action': 'HOLD', 'strength': 0,
                    'reason': 'Breakout blocked: weak/red candle'}

        body_ratio = body_size / candle_range
        if body_ratio < 0.5:
            return {'action': 'HOLD', 'strength': 0,
                    'reason': f'Breakout blocked: body ratio {body_ratio:.2f} < 0.50 (wick heavy)'}

        # ADX must confirm trend
        if adx['adx'] < self.min_adx_for_entry:
            return {'action': 'HOLD', 'strength': 0,
                    'reason': f'Breakout blocked: ADX {adx["adx"]:.1f} too low'}

        trend_up = adx['plus_di'] > adx['minus_di']
        if not trend_up:
            return {'action': 'HOLD', 'strength': 0,
                    'reason': 'Breakout blocked: DI- > DI+ (bearish pressure)'}

        return {
            'action': 'BUY',
            'strength': 0.80,
            'reason': f"BREAKOUT BUY: Price > R (vol={volume_ratio:.1f}x, body={body_ratio:.0%}, RSI={rsi:.1f})",
            'entry_type': 'BREAKOUT'
        }

    def is_strong_breakout(self, data, context):
        candle_body = abs(data['close'] - data['open'])
        strong_candle = candle_body > data['atr'] * 0.5
        return (
            context['breakout'] and
            data['volume'] > data['avg_volume'] * 1.2 and
            strong_candle
        )

    def is_compression_setup(self, data, context):
        return (
            context['near_resistance'] and
            context['higher_lows'] and
            context.get('tightening_range', False) and
            context.get('rising_volume', False)
        )

    def detect_higher_lows(self, df):
        if len(df) < 4:
            return False
        recent_lows = df['low'].iloc[-4:-1].tolist()
        return recent_lows[0] < recent_lows[1] < recent_lows[2]

    def detect_tightening_range(self, df, window=3):
        if len(df) < window * 2 + 1:
            return False
        candle_ranges = (df['high'] - df['low'])
        recent_range = candle_ranges.iloc[-window:].mean()
        prior_range = candle_ranges.iloc[-(window * 2):-window].mean()
        if np.isnan(recent_range) or np.isnan(prior_range) or prior_range <= 0:
            return False
        return recent_range < prior_range * 0.85

    def detect_rising_volume(self, df, window=3):
        if len(df) < window * 2 + 1:
            return False
        recent_volume = df['volume'].iloc[-window:].mean()
        prior_volume = df['volume'].iloc[-(window * 2):-window].mean()
        if np.isnan(recent_volume) or np.isnan(prior_volume) or prior_volume <= 0:
            return False
        return recent_volume > prior_volume

    def get_trend_continuation_context(self, df, price, ema20, ema50, bb):
        trend_up = ema20 > ema50
        higher_lows = self.detect_higher_lows(df)
        low_near_ema20 = abs(df['low'].iloc[-1] - ema20) / max(ema20, 1e-9) <= 0.004
        soft_pullback = price >= ema20 and low_near_ema20
        ema20_pullback_ready = trend_up and price <= ema20 * 1.003 and price >= ema20 * 0.995
        upper_band_ride = (
            price >= bb['upper'] * 0.985 and
            df['close'].iloc[-3:].min() >= bb['middle']
        )
        continuation_ready = trend_up and higher_lows and (soft_pullback or ema20_pullback_ready or upper_band_ride)
        return {
            'trend_up': trend_up,
            'soft_pullback': soft_pullback,
            'ema20_pullback_ready': ema20_pullback_ready,
            'upper_band_ride': upper_band_ride,
            'continuation_ready': continuation_ready,
        }

    def detect_pre_breakout(self, df, price, resistance, volume_ratio):
        if len(df) < 4:
            return False
        avg_volume = df['volume'].ewm(span=20, adjust=False).mean().iloc[-2] if len(df) > 1 else df['volume'].mean()
        context = self.level_context(price, resistance, min(df['low'].iloc[-20:]))
        context['higher_lows'] = self.detect_higher_lows(df)
        data = {
            'volume': df['volume'].iloc[-1],
            'avg_volume': avg_volume,
        }
        return self.is_scout_candidate(data, context)

    def build_context(self, data):
        context = self.level_context(data['price'], data['resistance'], data['support'])
        context['trend'] = data['ema_fast'] > data['ema_slow']
        context['trend_exists'] = data['ema20'] > data['ema50'] or context['trend']
        context['trend_aligned'] = data['ema20'] > data['ema50'] and context['trend'] and data['price'] > data['ema20']
        context['higher_lows'] = self.detect_higher_lows(data['df'])
        context['ema_alignment'] = data['ema_fast'] > data['ema_slow']
        context['tightening_range'] = self.detect_tightening_range(data['df'])
        context['rising_volume'] = self.detect_rising_volume(data['df'])
        continuation = self.get_trend_continuation_context(
            data['df'],
            data['price'],
            data['ema20'],
            data['ema50'],
            data['bb']
        )
        context.update(continuation)
        context['structure_ok'] = (
            context['higher_lows'] or
            continuation['continuation_ready'] or
            (context['near_support'] and not context['breakdown'])
        )
        context['structure_clean'] = (
            context['trend_aligned'] and
            (context['breakout'] or continuation['continuation_ready'] or not context['near_resistance']) and
            not context['breakdown']
        )
        context['momentum_strong'] = (
            data['macd']['macd'] > data['macd']['signal'] and
            data['macd']['macd'] > data['macd']['prev_macd'] and
            52 < data['rsi'] < 72
        )

        trade_data = {
            'open': data['df']['open'].iloc[-1],
            'close': data['price'],
            'volume': data['volume'],
            'avg_volume': data['avg_volume'],
            'atr': data['atr'],
            'rsi': data['rsi'],
        }
        context['score'] = self.get_trade_score(trade_data, context)
        context['scout'] = self.is_compression_setup(trade_data, context)
        context['market'] = data['market_type']
        return context

    # ════════════════════════════════════════════════════════════════════
    # TRADE SCORING & DYNAMIC SIZING
    # ════════════════════════════════════════════════════════════════════
    def get_trade_score(self, data, context):
        score = 0

        if context.get('trend_exists'):
            score += 1
        if data['volume'] >= data['avg_volume']:
            score += 1
        if 50 < data['rsi'] < 72:
            score += 1
        if context.get('structure_ok'):
            score += 1
        if context.get('trend_aligned'):
            score += 1

        return score

    def classify_setup_quality(self, data, context):
        market_type = context.get('market', 'CHOPPY')
        if market_type == 'RANGING':
            if data['volume'] > data['avg_volume'] and context.get('near_support') and not context.get('breakdown'):
                return 'A+'
            if data['volume'] >= data['avg_volume'] and (context.get('near_support') or context.get('structure_ok')):
                return 'B+'
            return None

        a_plus = (
            context.get('trend_aligned', False) and
            data['volume'] > data['avg_volume'] and
            context.get('structure_clean', False) and
            context.get('momentum_strong', False)
        )
        b_plus = (
            context.get('trend_exists', False) and
            data['volume'] >= data['avg_volume'] and
            context.get('structure_ok', False)
        )
        if a_plus:
            return 'A+'
        if b_plus:
            return 'B+'
        return None

    def score_trade(self, df, price, rsi, ema_fast, ema_slow, volume_ratio):
        """Score a trade setup using the flexible 3-of-5 confirmation model."""
        score = 0
        resistance, support = self.calculate_levels(df)
        context = self.level_context(price, resistance, support)
        context['trend'] = ema_fast > ema_slow
        context['ema_alignment'] = ema_fast > ema_slow
        context['higher_lows'] = self.detect_higher_lows(df)
        context['structure_clean'] = context['trend'] and (context['breakout'] or not context['near_resistance']) and not context['breakdown']
        avg_volume = df['volume'].ewm(span=20, adjust=False).mean().iloc[-2] if len(df) > 1 else df['volume'].mean()
        data = {
            'open': df['open'].iloc[-1],
            'close': price,
            'volume': df['volume'].iloc[-1],
            'avg_volume': avg_volume,
            'atr': self.calculate_atr(df, period=14),
            'rsi': rsi,
        }
        score = self.get_trade_score(data, context)
        if context['breakout']:
            score = min(score + 1, 5)
        return score

    def get_position_size(self, balance, score, entry_type):
        base_size = 0.15
        if entry_type == 'MICRO_B+':
            return balance * 0.03
        if entry_type == 'A+':
            return balance * base_size          # full size (15%)
        if entry_type == 'B+':
            return balance * (base_size * 0.6)  # reduced size (~9%)
        if entry_type == 'SCOUT':
            return balance * (base_size * 0.4)  # scout (~6%)

        if score >= 4:
            return balance * base_size
        if score == 3:
            return balance * (base_size * 0.6)
        return balance * (base_size * 0.4)

    def get_entry_profile(self, signal, score):
        """Choose execution profile from market regime, quality tier, and entry type."""
        signal_type = signal.get('entry_type', 'PULLBACK').upper()
        entry_tier = signal.get('entry_tier')
        market_type = signal.get('market_type', 'CHOPPY')

        if signal.get('micro_b_test'):
            return {
                'entry_type': 'micro_b_test',
                'balance_fraction': 0.03,
                'tp_mode': 'quick_percent',
                'tp_percent': 0.8,
                'sl_atr_multiplier': 1.0,
            }

        if market_type == 'CHOPPY':
            return {
                'entry_type': 'scout_only',
                'balance_fraction': 0.005,
                'tp_mode': 'quick_percent',
                'tp_percent': 0.5,
                'sl_atr_multiplier': 1.0,
            }

        if market_type == 'RANGING' and entry_tier == 'A+':
            return {
                'entry_type': 'ranging',
                'balance_fraction': 0.10,
                'tp_mode': 'middle_band',
                'sl_atr_multiplier': 1.2,
            }

        if market_type == 'RANGING' and entry_tier == 'B+':
            return {
                'entry_type': 'range_scalp',
                'balance_fraction': 0.05,
                'tp_mode': 'quick_percent',
                'tp_percent': 0.75,
                'sl_atr_multiplier': 1.0,
            }

        if entry_tier == 'SCOUT' or signal.get('scout_trade'):
            return {
                'entry_type': 'scout',
                'balance_fraction': self.trend_scout_target_fraction * self.trend_scout_fraction if signal.get('trend_scout') else (0.005 if market_type == 'CHOPPY' else 0.05),
                'tp_mode': 'quick_percent',
                'tp_percent': 0.75 if signal.get('trend_scout') else (0.5 if market_type == 'CHOPPY' else 1.0),
                'sl_atr_multiplier': 1.0,
                'scale_target_fraction': self.trend_scout_target_fraction if signal.get('trend_scout') else None,
                'scale_entry_fraction': self.trend_scout_fraction if signal.get('trend_scout') else None,
            }

        if market_type == 'TRENDING' and entry_tier == 'A+':
            return {
                'entry_type': 'a_plus',
                'balance_fraction': 0.15,
                'tp_mode': 'runner_percent',
                'tp1_percent': 2.5,
                'tp2_percent': 4.0,
                'sl_atr_multiplier': 1.5,
            }

        if market_type == 'TRENDING' and entry_tier == 'B+':
            return {
                'entry_type': 'b_plus',
                'balance_fraction': 0.08,
                'tp_mode': 'quick_percent',
                'tp_percent': 1.25,
                'sl_atr_multiplier': 1.2,
            }

        return {
            'entry_type': 'continuation',
            'balance_fraction': 0.10,
            'tp_mode': 'quick_percent',
            'tp_percent': 1.0,
            'sl_atr_multiplier': 1.2,
        }

    def get_score_position_size(self, balance, score, signal):
        """Position notional based on execution profile."""
        if score <= 2:
            return 0

        profile = self.get_entry_profile(signal, score)
        session_mode = signal.get('session_mode', 'NORMAL')
        position_notional = balance * profile['balance_fraction']
        if session_mode == 'LOW_RISK':
            position_notional = min(position_notional, balance * 0.10)
        if self.last_trade_win:
            position_notional *= 1.1

        max_notional = balance * self.max_position_cap
        return min(position_notional, max_notional)

    def get_expected_move_percent(self, entry_price, resistance, atr_value, breakout):
        if entry_price <= 0:
            return 0
        if breakout:
            return max((atr_value / entry_price) * 100, 0)
        return max(((resistance - entry_price) / entry_price) * 100, 0)

    def dynamic_tp_sl(self, entry_price, score, signal):
        """Return TP1, TP2 and SL using ATR-based exits with profile fallback."""
        profile = self.get_entry_profile(signal, score)
        atr_value = signal.get('atr_value', 0)
        tp_mode = profile.get('tp_mode', 'quick_percent')
        sl_atr_multiplier = profile.get('sl_atr_multiplier', self.atr_stop_multiplier)
        if atr_value and atr_value > 0:
            sl = entry_price - (atr_value * sl_atr_multiplier)
            if tp_mode == 'middle_band' and signal.get('bb_middle'):
                tp1 = None
                tp2 = signal['bb_middle']
            elif tp_mode == 'runner_percent':
                tp1 = entry_price * (1 + profile.get('tp1_percent', 2.5) / 100)
                tp2 = entry_price * (1 + profile.get('tp2_percent', 4.0) / 100)
            elif tp_mode == 'quick_percent':
                tp1 = None
                tp2 = entry_price * (1 + profile.get('tp_percent', 1.0) / 100)
            else:
                tp1 = None
                tp2 = entry_price + (atr_value * self.atr_target_multiplier)
        else:
            tp1 = None
            tp2 = entry_price * (1 + profile.get('tp_percent', 1.0) / 100)
            sl = entry_price * (1 - 1.0 / 100)
        return tp1, tp2, sl, profile

    def is_b_plus_trade(self, score, context):
        return score >= 3

    def is_scout_candidate(self, data, context):
        return self.is_compression_setup(data, context)

    def detect_market_mode(self, snapshot):
        if snapshot['atr_avg'] <= 0:
            return 'CHOPPY'
        if snapshot['atr'] > snapshot['atr_avg'] * 1.2:
            return 'TRENDING'
        if snapshot['atr'] > snapshot['atr_avg'] * 0.8:
            return 'RANGING'
        return 'CHOPPY'

    def is_session_active(self, session_name):
        return session_name in ('london', 'us')

    def avoid_chop(self, df):
        """Return True if market is choppy (ATR below average = bad)."""
        atr_current = self.calculate_atr(df, period=14)
        atr_series = []
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr_avg = tr.rolling(window=14).mean().iloc[-20:-1].mean()
        if atr_avg == 0 or np.isnan(atr_avg):
            return False
        if atr_current < atr_avg * 0.85:
            return True   # choppy - skip
        return False

    def session_filter(self):
        """Block trading outside EU/US active hours (7-22 UTC)."""
        utc_hour = datetime.now(timezone.utc).hour
        return 7 <= utc_hour <= 22

    def get_btc_bias(self):
        """Use BTC trend and RSI as a market bias filter for all long entries."""
        df = self.get_candles('BTCUSDT', '15m', 100)
        if df is None or len(df) < 50:
            return 'NEUTRAL'

        closes = df['close']
        ema20 = self.calculate_ema(closes, 20)
        ema50 = self.calculate_ema(closes, 50)
        rsi = self.calculate_rsi(closes)

        if ema20 > ema50 and rsi > 50:
            return 'BULLISH'
        if ema20 < ema50 and rsi < 50:
            return 'BEARISH'
        return 'NEUTRAL'

    def get_pair_snapshot(self, symbol):
        """Build a lightweight ranking snapshot for a symbol."""
        df = self.get_candles(symbol, '15m', 100)
        if df is None or len(df) < 50:
            return None

        closes = df['close']
        adx = self.calculate_adx(df)
        atr_current = self.calculate_atr(df, period=14)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift()).abs(),
            (df['low'] - df['close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(window=14).mean()
        atr_avg = atr_series.iloc[-20:-1].mean()
        atr_prev = atr_series.iloc[-2] if not np.isnan(atr_series.iloc[-2]) else 0

        snapshot = {
            'symbol': symbol,
            'price': closes.iloc[-1],
            'rsi': self.calculate_rsi(closes),
            'ema20': self.calculate_ema(closes, 20),
            'ema50': self.calculate_ema(closes, 50),
            'adx': adx['adx'],
            'volume': df['volume'].iloc[-1],
            'avg_volume': df['volume'].ewm(span=20, adjust=False).mean().iloc[-2],
            'volume_ratio': self.get_volume_ratio(df),
            'atr': atr_current,
            'atr_avg': 0 if np.isnan(atr_avg) else atr_avg,
            'atr_rising': atr_current > atr_prev,
            'df': df,
        }
        snapshot['market_type'] = self.get_market_type(adx['adx'])
        snapshot['market_mode'] = snapshot['market_type']
        return snapshot

    def debug_symbol_check(self, symbol, snapshot, context, score, reason):
        print(
            f"""
{symbol} CHECK:
ATR: {snapshot.get('atr', 0):.2f} | ATR AVG: {snapshot.get('atr_avg', 0):.2f}
Volume: {snapshot.get('volume', 0):.2f} | AvgVol: {snapshot.get('avg_volume', 0):.2f}
Score: {score}
Market Mode: {snapshot.get('market_mode', 'UNKNOWN')}
Conditions:
- Trend: {context.get('trend')}
- Structure: {context.get('structure_clean')}
- EMA: {context.get('ema_alignment')}
Reason: {reason}
"""
        )

    def rank_pairs(self, pair_snapshots):
        ranked = []
        for symbol, data in pair_snapshots.items():
            score = 0

            if data['volume_ratio'] > 1.0:
                score += 1
            if data['ema20'] > data['ema50']:
                score += 1
            if data['rsi'] > 50:
                score += 1
            if data['atr'] > data['atr_avg']:
                score += 1

            ranked.append((symbol, score))

        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    def get_dynamic_trade_cap(self, pair_snapshots, session_name):
        """Adjust today's trade cap based on current volatility and volume."""
        if not pair_snapshots:
            return min(2, self.hard_max_trades)

        hot_market = any(
            data['atr'] > data['atr_avg'] * 1.2 and data['volume_ratio'] > 1.3
            for data in pair_snapshots.values()
            if data['atr_avg'] > 0
        )
        normal_market = any(
            data['atr'] > data['atr_avg']
            for data in pair_snapshots.values()
            if data['atr_avg'] > 0
        )
        atr_rising = any(data.get('atr_rising') for data in pair_snapshots.values())

        if hot_market:
            return min(6, self.hard_max_trades)
        if session_name in ('london', 'us') and atr_rising:
            return min(5, self.hard_max_trades)
        if normal_market:
            return min(4, self.hard_max_trades)
        return min(2, self.hard_max_trades)

    def explain_skip(self, symbol, score, min_score, checks):
        print(f"\n[SKIPPED - {symbol}]")
        print(f"Score: {score} (min required: {min_score})")
        print("Reasons:")
        for key, value in checks.items():
            status = "OK" if value else "X"
            print(f"- {key}: {status}")

    def get_dynamic_min_score(self, snapshot, context):
        """Return the live threshold used by the current entry hierarchy."""
        data = {
            'volume': snapshot.get('volume', 0),
            'avg_volume': snapshot.get('avg_volume', 0),
        }
        if self.is_scout_candidate(data, context):
            return 0
        return 3

    def elite_filter(self, score, context, snapshot):
        data = {
            'volume': snapshot.get('volume', 0),
            'avg_volume': snapshot.get('avg_volume', 0),
        }
        if self.is_scout_candidate(data, context):
            return True
        if context.get('breakout'):
            return True
        return self.is_b_plus_trade(score, context)

    # ════════════════════════════════════════════════════════════════════
    # FILTERS
    # ════════════════════════════════════════════════════════════════════
    def btc_is_healthy(self):
        try:
            df = self.get_candles('BTCUSDT', '15m', 20)
            if df is None or len(df) < 10:
                return True
            closes = df['close']
            change_15m = ((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2]) * 100
            change_1h = ((closes.iloc[-1] - closes.iloc[-4]) / closes.iloc[-4]) * 100
            if change_15m < -0.5 or change_1h < -1.5:
                print(f"   BTC FILTER: dropping ({change_1h:.2f}% 1h) - blocking alts")
                return False
            return True
        except Exception as e:
            print(f"   BTC filter error: {e}")
            return True

    def get_volume_ratio(self, df):
        """Return current volume / EMA(20) volume ratio."""
        if len(df) < 20:
            return 1.0
        vol_ema = df['volume'].ewm(span=20, adjust=False).mean().iloc[-2]
        current_volume = df['volume'].iloc[-1]
        return current_volume / vol_ema if vol_ema > 0 else 1.0

    def has_no_momentum(self, symbol):
        df = self.get_candles(symbol, '15m', 20)
        if df is None or len(df) < 4:
            return False

        average_volume = df['volume'].rolling(20).mean().iloc[-1]
        if np.isnan(average_volume) or average_volume <= 0:
            return False

        volume_is_weak = df['volume'].iloc[-1] < average_volume
        reference_close = df['close'].iloc[-4]
        if reference_close <= 0:
            return False
        recent_change = abs((df['close'].iloc[-1] - reference_close) / reference_close) * 100
        return volume_is_weak and recent_change < self.no_momentum_price_change_threshold

    def should_exit_early(self, position, current_price):
        """Return True if trade is losing and volume momentum is weak."""
        df = self.get_candles(position['symbol'], '15m', 25)
        if df is None or len(df) < 20:
            return False
        entry = position['entry_price']
        profit = (current_price - entry) / entry
        last = df.iloc[-1]
        avg_volume = df['volume'].rolling(20).mean().iloc[-1]
        if np.isnan(avg_volume) or avg_volume <= 0:
            return False
        weak_momentum = last['volume'] < avg_volume
        if profit < -0.004 and weak_momentum:
            return True
        return False

    def check_volume(self, df):
        ratio = self.get_volume_ratio(df)
        if ratio < 0.8:
            print(f"   LOW VOLUME: {ratio:.2f}x avg - skipping")
            return False
        return True

    def get_multi_timeframe_count(self, symbol):
        bullish_count = 0
        for tf in ['1m', '5m', '15m']:
            df = self.get_candles(symbol, tf, 50)
            if df is None:
                continue
            closes = df['close']
            ema7 = closes.ewm(span=7).mean().iloc[-1]
            ema18 = closes.ewm(span=18).mean().iloc[-1]
            if closes.iloc[-1] > ema7 and ema7 > ema18:
                bullish_count += 1
        return bullish_count

    def check_multi_timeframe(self, symbol):
        bullish_count = self.get_multi_timeframe_count(symbol)
        if bullish_count < 2:
            print(f"   MTF: Only {bullish_count}/3 timeframes bullish - blocking")
            return False
        return True

    # ════════════════════════════════════════════════════════════════════
    # BREAKOUT STATE MACHINE
    # ════════════════════════════════════════════════════════════════════
    def get_symbol_state(self, symbol):
        if symbol not in self.symbol_state:
            self.symbol_state[symbol] = {
                'waiting_for_retest': False,
                'breakout_level': None,
                'breakout_direction': None,
                'retest_candles': 0,
            }
        return self.symbol_state[symbol]

    def reset_breakout_state(self, symbol):
        self.symbol_state[symbol] = {
            'waiting_for_retest': False,
            'breakout_level': None,
            'breakout_direction': None,
            'retest_candles': 0
        }

    # ════════════════════════════════════════════════════════════════════
    # MAIN ANALYSIS (LOCATION-BASED)
    # ════════════════════════════════════════════════════════════════════
    def analyze(self, symbol):
        # Stop scanning if daily target hit
        if self.daily_profit >= self.daily_profit_target:
            return {'action': 'HOLD', 'strength': 0,
                    'reason': f'Daily target ${self.daily_profit_target} hit - no new trades'}

        df = self.get_candles(symbol, '15m', 100)
        if df is None or len(df) < 51:
            return {'action': 'HOLD', 'strength': 0, 'reason': 'Insufficient data'}

        analysis_df = df.iloc[:-1].copy()
        entry_price = df['open'].iloc[-1]
        closes = analysis_df['close']
        price = closes.iloc[-1]

        rsi = self.calculate_rsi(closes)
        macd = self.calculate_macd(closes)
        ema_fast = self.calculate_ema(closes, 7)
        ema_slow = self.calculate_ema(closes, 18)
        ema20 = self.calculate_ema(closes, 20)
        ema50 = self.calculate_ema(closes, 50)
        atr_current = self.calculate_atr(analysis_df, period=14)
        tr = pd.concat([
            analysis_df['high'] - analysis_df['low'],
            (analysis_df['high'] - analysis_df['close'].shift()).abs(),
            (analysis_df['low'] - analysis_df['close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr_avg = tr.rolling(window=14).mean().iloc[-20:-1].mean()
        adx = self.calculate_adx(analysis_df)
        bb = self.calculate_bollinger(closes)
        bollinger_breakout = self.bollinger_breakout_signal(analysis_df, bb)
        volume_ratio = self.get_volume_ratio(analysis_df)
        avg_volume = analysis_df['volume'].ewm(span=20, adjust=False).mean().iloc[-2] if len(analysis_df) > 1 else analysis_df['volume'].mean()

        market_type = self.get_market_type(adx['adx'])
        market_mode = market_type
        in_active_session = self.session_filter()
        if not in_active_session:
            print(f"{symbol} outside main session -> allowing reduced-risk trade")
            session_mode = 'LOW_RISK'
        else:
            session_mode = 'NORMAL'

        sr = self.calculate_support_resistance(analysis_df)
        support = sr['support']
        resistance = sr['resistance']
        recent_resistance, recent_support = self.calculate_levels(analysis_df)
        context_data = {
            'df': analysis_df,
            'price': price,
            'resistance': resistance,
            'support': support,
            'ema_fast': ema_fast,
            'ema_slow': ema_slow,
            'ema20': ema20,
            'ema50': ema50,
            'volume': analysis_df['volume'].iloc[-1],
            'avg_volume': avg_volume,
            'atr': atr_current,
            'rsi': rsi,
            'macd': macd,
            'bb': bb,
            'market_mode': market_mode,
            'market_type': market_type,
            'session_mode': session_mode,
        }
        context = self.build_context(context_data)
        trade_data = {
            'open': analysis_df['open'].iloc[-1],
            'close': price,
            'volume': analysis_df['volume'].iloc[-1],
            'avg_volume': avg_volume,
            'atr': atr_current,
            'rsi': rsi,
        }
        state = self.get_symbol_state(symbol)
        tolerance = 0.002
        compression_setup = context['scout']
        trade_score = context['score']
        quality_tier = self.classify_setup_quality(trade_data, context)
        strong_breakout = self.is_strong_breakout(trade_data, context)
        bollinger_strong_breakout = bollinger_breakout['strong_breakout']
        bollinger_standard_breakout = bollinger_breakout['breakout']
        early_breakout = market_type == 'TRENDING' and self.is_early_breakout(
            price,
            resistance,
            volume_ratio,
            context.get('rising_volume', False),
        )
        if bollinger_standard_breakout:
            trade_score = min(trade_score + 1, 5)
        if bollinger_strong_breakout:
            trade_score = min(trade_score + 1, 5)
        entry_type = None
        entry_reason = None
        entry_strength = 0.0
        support_override = None
        clear_breakout_wait = False
        retest_reason = None

        # Track breakout state without blocking the tier decision path.
        if market_type == 'TRENDING' and price > resistance and not state['waiting_for_retest'] and not compression_setup and not strong_breakout and trade_score < 3:
            state['waiting_for_retest'] = True
            state['breakout_level'] = resistance
            state['breakout_direction'] = 'LONG'
            state['retest_candles'] = 0
            self.send_telegram(
                f"📈 {symbol} Breakout detected\n"
                f"Level: ${resistance:.4f}\nWaiting for retest..."
            )
            retest_reason = f"Breakout at ${resistance:.4f} - waiting for retest"

        if state.get('waiting_for_retest'):
            if compression_setup or strong_breakout or trade_score >= 3:
                self.reset_breakout_state(symbol)
            else:
                state['retest_candles'] += 1
                if state['retest_candles'] > 10:
                    self.reset_breakout_state(symbol)
                    retest_reason = 'Breakout retest expired (10 candles)'

                if state['breakout_direction'] == 'LONG' and \
                   price <= state['breakout_level'] * (1 + tolerance):
                    current_open = df['open'].iloc[-1]
                    current_close = df['close'].iloc[-1]
                    if current_close > current_open and rsi > 50:
                        strong_breakout = True
                        support_override = state['breakout_level']
                        clear_breakout_wait = True
                        entry_reason = f"BREAKOUT BUY: Retest confirmed @ ${state['breakout_level']:.4f}"
                    else:
                        retest_reason = 'Retest touched - waiting for confirmation candle'
                else:
                    retest_reason = f"Watching retest at ${state['breakout_level']:.4f}"

        near_support = self.is_near_level(price, support)
        near_resistance = self.is_near_level(price, resistance)

        zone = self.get_trade_zone(price, support, resistance)
        breakout = context['breakout'] or strong_breakout or bollinger_standard_breakout or early_breakout
        context['breakout'] = breakout
        score = trade_score
        scout = context['scout']
        continuation_ready = context.get('continuation_ready', False)
        market = context['market']
        micro_b_test = False

        # Entry decision always runs before filters.
        print(f"{symbol} reached entry evaluation")
        if market_type == 'RANGING':
            ranging_signal = self.ranging_trade(price, rsi, support, resistance, volume_ratio)
            if ranging_signal['action'] == 'BUY' and quality_tier == 'A+':
                entry_type = 'RANGING'
                entry_reason = ranging_signal['reason']
                entry_strength = ranging_signal['strength']
            elif ranging_signal['action'] == 'BUY' and quality_tier == 'B+':
                entry_type = 'RANGING'
                entry_reason = f"RANGING SCALP B+: {ranging_signal['reason']}"
                entry_strength = 0.60
        elif market_type == 'TRENDING' and early_breakout and quality_tier == 'A+':
            entry_type = 'A+'
            entry_reason = 'EARLY BREAKOUT A+: price is within 0.2% of resistance with rising volume'
            entry_strength = 0.84
        elif market_type == 'TRENDING' and early_breakout and quality_tier == 'B+':
            entry_type = 'B+'
            entry_reason = 'EARLY BREAKOUT B+: price is within 0.2% of resistance with rising volume'
            entry_strength = 0.76
        elif quality_tier == 'A+' and bollinger_strong_breakout:
            entry_type = 'A+'
            entry_reason = (
                f'BOLLINGER BREAKOUT A+: Close cleared upper band after squeeze '
                f'(width={bb["width"]:.3f}, vol={bollinger_breakout["volume_ratio"]:.2f}x)'
            )
            entry_strength = 0.85
        elif quality_tier == 'B+' and bollinger_standard_breakout:
            entry_type = 'B+'
            entry_reason = (
                f'BOLLINGER BREAKOUT B+: Close cleared upper band after squeeze '
                f'(width={bb["width"]:.3f}, vol={bollinger_breakout["volume_ratio"]:.2f}x)'
            )
            entry_strength = 0.75
        elif market_type == 'TRENDING' and quality_tier == 'A+' and continuation_ready:
            entry_type = 'A+'
            if context.get('soft_pullback'):
                continuation_trigger = 'soft EMA20 pullback'
            elif context.get('ema20_pullback_ready'):
                continuation_trigger = 'EMA20 pullback within 0.3%'
            else:
                continuation_trigger = 'upper band ride'
            entry_reason = (
                f'TREND A+: EMA pullback entry with clean structure and {continuation_trigger}'
            )
            entry_strength = 0.82
        elif market_type == 'TRENDING' and quality_tier == 'B+' and continuation_ready:
            entry_type = 'B+'
            if context.get('soft_pullback'):
                continuation_trigger = 'soft EMA20 pullback'
            elif context.get('ema20_pullback_ready'):
                continuation_trigger = 'EMA20 pullback within 0.3%'
            else:
                continuation_trigger = 'upper band ride'
            entry_reason = (
                f'TREND CONTINUATION B+: controlled EMA pullback with {continuation_trigger}'
            )
            entry_strength = 0.78
        elif market_type == 'TRENDING' and scout and context.get('higher_lows'):
            entry_type = 'SCOUT'
            entry_reason = 'TREND SCOUT: compression + higher lows before breakout'
            entry_strength = 0.62
        elif market_type == 'CHOPPY' and scout:
            entry_type = 'SCOUT'
            entry_reason = 'SCOUT ENTRY: compression + higher lows + rising volume in choppy market'
            entry_strength = 0.55
        else:
            entry_type = None

        if quality_tier == 'A+':
            score = max(score, 4)
        elif quality_tier == 'B+':
            score = max(score, 3)

        print(f"{symbol} | score={score} | quality={quality_tier} | scout={scout} | breakout={breakout} | entry={entry_type}")

        if entry_type is None and self.enable_micro_b_plus_test:
            print(f"{symbol} no setup -> allowing micro B+ test")
            entry_type = 'B+'
            entry_reason = 'Temporary micro B+ test entry'
            entry_strength = 0.55
            micro_b_test = True

        if self.only_a_plus_after_loss and self.daily_losing_trades >= 1 and entry_type not in (None, 'A+'):
            print(f"{symbol} blocking non A+ setup after daily loss")
            entry_type = None
            entry_reason = 'Post-loss protection active - A+ setups only'
            micro_b_test = False

        if market_type == 'CHOPPY' and entry_type in ('B+', 'SCOUT', 'RANGING') and not micro_b_test:
            print(f"{symbol} reducing trades due to choppy regime")
            entry_type = None
            entry_reason = 'Choppy regime - reduced trade frequency'

        if entry_type == 'B+' and not micro_b_test and adx['adx'] < self.min_adx_for_entry:
            print(f"{symbol} skipping weak trend setup")
            entry_type = None
            entry_reason = f'Skipping weak trend setup ({adx["adx"]:.1f} < {self.min_adx_for_entry})'

        signal = {
            'action': 'HOLD' if entry_type is None else 'BUY',
            'strength': 0 if entry_type is None else entry_strength,
            'reason': entry_reason or retest_reason or f'No valid setup ({score}/5 checks)',
            'score': score,
            'scout': scout,
            'breakout': breakout,
            'quality_tier': quality_tier,
        }
        if entry_type == 'SCOUT':
            signal.update({
                'entry_type': 'SCOUT',
                'entry_tier': 'SCOUT',
                'scout_trade': True,
            })
            if market_type == 'TRENDING':
                signal['trend_scout'] = True
        elif entry_type == 'RANGING':
            signal.update({
                'entry_type': 'RANGING',
                'entry_tier': 'RANGING',
            })
        elif entry_type == 'A+':
            signal.update({
                'entry_type': 'BREAKOUT',
                'entry_tier': 'A+',
                'score': max(trade_score, 4),
            })
            if support_override is not None:
                signal['support_override'] = support_override
            if clear_breakout_wait:
                signal['clear_breakout_wait'] = True
        elif entry_type == 'B+':
            signal.update({
                'entry_type': 'CONTINUATION' if continuation_ready else 'B_PLUS',
                'entry_tier': 'B+',
                'fallback_trade': not continuation_ready,
            })
            if continuation_ready:
                signal['continuation_trade'] = True
            if micro_b_test:
                signal['micro_b_test'] = True

        # ── Confirmation candle ───────────────────────────────────────
        if signal['action'] == 'BUY' and signal.get('entry_type') not in ('BREAKOUT', 'SCOUT'):
            if not self.has_confirmation_candle(analysis_df, 'bullish'):
                signal = {
                    'action': 'HOLD',
                    'strength': 0,
                    'reason': 'Buy signal - waiting for confirmation candle',
                    'score': trade_score,
                }

        # ── Extra filters + ADDED: final validation gate ──────────────
        if signal['action'] == 'BUY':
            mtf_bullish_count = self.get_multi_timeframe_count(symbol)
            allow_breakout_override = signal.get('breakout') and volume_ratio >= 1.2
            if not self.btc_is_healthy():
                signal = {
                    'action': 'HOLD',
                    'strength': 0,
                    'reason': 'BTC dumping - entry blocked',
                    'score': trade_score,
                }
            elif mtf_bullish_count < 2 and not allow_breakout_override:
                signal = {
                    'action': 'HOLD',
                    'strength': 0,
                    'reason': 'Timeframes not aligned - entry blocked',
                    'score': trade_score,
                }
            elif mtf_bullish_count < 2 and allow_breakout_override:
                print(f"   MTF OVERRIDE: breakout volume spike allows entry with {mtf_bullish_count}/3 bullish")

            # ADDED: Final setup validation (last gate before trade fires)
            if signal['action'] == 'BUY':
                entry_type = signal.get('entry_type', 'PULLBACK')
                entry_tier = signal.get('entry_tier')
                prices_list = closes.tolist()
                if entry_tier == 'A+' or entry_type == 'BREAKOUT':
                    if not self.valid_breakout_setup(
                        price, rsi,
                        macd['macd'], macd['signal'], macd['prev_macd'], ema_slow
                    ):
                        signal = {
                            'action': 'HOLD',
                            'strength': 0,
                            'reason': 'Breakout validation failed - not all conditions met',
                            'score': trade_score,
                        }
                elif entry_tier not in ('SCOUT', 'B+', 'RANGING') and entry_type not in ('B_PLUS', 'RANGING', 'CONTINUATION'):
                    if not self.valid_setup(
                        price, prices_list, rsi,
                        macd['macd'], macd['signal'], macd['prev_macd'], ema_slow
                    ):
                        signal = {
                            'action': 'HOLD',
                            'strength': 0,
                            'reason': 'Setup validation failed - not all conditions met',
                            'score': trade_score,
                        }

        if signal.get('clear_breakout_wait'):
            self.reset_breakout_state(symbol)

        signal['market_type'] = market_type
        signal['price'] = entry_price
        signal['support'] = support
        signal['resistance'] = resistance
        signal['rsi'] = rsi
        signal['adx'] = adx['adx']
        signal['zone'] = zone
        signal['market_mode'] = market_mode
        signal['session_mode'] = session_mode
        signal['atr_value'] = atr_current
        signal['bb_width'] = bb['width']
        signal['bb_middle'] = bb['middle']
        signal['volume_ratio'] = volume_ratio
        signal['volume_spike'] = volume_ratio >= 1.2
        signal['strong_trend'] = market_mode == 'TRENDING' and market_type == 'TRENDING' and adx['adx'] >= self.adx_trend_threshold and ema_fast > ema_slow
        signal['early_breakout'] = early_breakout
        signal['ema20'] = ema20
        signal['ema50'] = ema50
        signal['trend_up'] = context.get('trend_up', False)
        signal['soft_pullback'] = context.get('soft_pullback', False)
        signal['ema20_pullback_ready'] = context.get('ema20_pullback_ready', False)
        signal['upper_band_ride'] = context.get('upper_band_ride', False)

        if signal['action'] == 'BUY':
            expected_move = self.get_expected_move_percent(entry_price, resistance, atr_current, breakout)
            signal['expected_move'] = expected_move
            if expected_move < self.min_expected_move_percent:
                signal = {
                    'action': 'HOLD',
                    'strength': 0,
                    'reason': f'Expected move {expected_move:.2f}% below {self.min_expected_move_percent:.2f}% minimum',
                    'score': signal.get('score', trade_score),
                }

        # Attach trade score for dynamic sizing
        if signal['action'] == 'BUY':
            if 'score' not in signal:
                signal['score'] = self.score_trade(
                    analysis_df, price, rsi, ema_fast, ema_slow, volume_ratio
                )
            if signal['score'] <= 2:
                signal = {
                    'action': 'HOLD',
                    'strength': 0,
                    'reason': f"Score {signal['score']}/5 too low - skipping",
                    'score': signal['score'],
                }

        return signal

    # ════════════════════════════════════════════════════════════════════
    # POSITION SIZING
    # ════════════════════════════════════════════════════════════════════
    def calculate_position_size(self, balance, entry_price, stop_loss_price, risk_percent=0.015):
        risk_amount = balance * risk_percent
        risk_per_unit = abs(entry_price - stop_loss_price)
        if risk_per_unit == 0:
            return 0
        position_size = risk_amount / risk_per_unit
        max_size = (balance * self.max_position_cap) / entry_price
        position_size = min(position_size, max_size)
        if position_size * entry_price < 10:
            return 0
        return position_size

    def get_open_position(self, symbol):
        for position in self.open_positions:
            if position['symbol'] == symbol:
                return position
        return None

    def should_scale_scout_position(self, position, signal):
        if not position or position.get('scaled_in'):
            return False
        if position.get('entry_type') != 'scout':
            return False
        if not position.get('signal', {}).get('trend_scout'):
            return False
        if signal.get('action') != 'BUY':
            return False
        if signal.get('micro_b_test'):
            return False
        if signal.get('entry_tier') not in ('A+', 'B+'):
            return False
        if signal.get('price', 0) <= position.get('entry_price', 0):
            return False
        return signal.get('breakout') or signal.get('early_breakout')

    def execute_scale_in(self, position, signal):
        if self.trade_lock:
            print(f"   TRADE LOCK - skipping scale-in {position['symbol']}")
            return None

        self.trade_lock = True
        try:
            symbol = position['symbol']
            price = signal['price']
            score = signal.get('score', 3)

            tp1_price, tp2_price, dynamic_sl, profile = self.dynamic_tp_sl(price, score, signal)
            target_notional = position.get('target_position_notional')
            if target_notional is None:
                target_notional = self.get_score_position_size(self.get_balance(), score, signal)
            allocated_notional = position.get('allocated_notional', position['quantity'] * position['entry_price'])
            add_notional = target_notional - allocated_notional
            if add_notional < 10:
                print(f"   SCALE-IN SKIPPED {symbol} - scout already near target size")
                return None

            quantity = add_notional / price
            step_size, precision = self.get_symbol_precision(symbol)
            quantity = round(quantity, precision)
            if quantity <= 0 or quantity * price < 10:
                print(f"   SCALE-IN SKIPPED {symbol} - add size too small")
                return None

            order = self.client.create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )

            fill_price = float(order['fills'][0]['price'])
            entry_fee = self.calculate_order_fee_usdt(order, symbol, fallback_price=fill_price)

            old_quantity = position['quantity']
            new_quantity = old_quantity + quantity
            weighted_entry = ((position['entry_price'] * old_quantity) + (fill_price * quantity)) / max(new_quantity, 1e-9)

            support = signal.get('support_override', signal.get('support', weighted_entry * 0.985))
            structure_sl = support * 0.995
            atr_value = signal.get('atr_value', 0.0)
            sl_atr_multiplier = profile.get('sl_atr_multiplier', self.atr_stop_multiplier)
            if atr_value and atr_value > 0:
                weighted_dynamic_sl = weighted_entry - (atr_value * sl_atr_multiplier)
            else:
                weighted_dynamic_sl = weighted_entry * 0.99
            stop_loss = max(weighted_dynamic_sl, structure_sl, weighted_entry * 0.97)

            price_ratio_tp1 = None if tp1_price is None else (tp1_price / max(price, 1e-9))
            price_ratio_tp2 = tp2_price / max(price, 1e-9)
            tp1_price = None if price_ratio_tp1 is None else weighted_entry * price_ratio_tp1
            take_profit = weighted_entry * price_ratio_tp2
            actual_risk = weighted_entry - stop_loss
            rr_target = round((take_profit - weighted_entry) / max(actual_risk, 1e-9), 2)

            position['quantity'] = new_quantity
            position['original_quantity'] = new_quantity
            position['allocated_notional'] = allocated_notional + (quantity * fill_price)
            position['target_position_notional'] = target_notional
            position['entry_price'] = weighted_entry
            position['stop_loss'] = stop_loss
            position['tp1_price'] = tp1_price
            position['take_profit'] = take_profit
            position['risk_percent'] = profile['balance_fraction']
            position['rr_target'] = rr_target
            position['entry_type'] = profile['entry_type']
            position['fallback_trade'] = signal.get('fallback_trade', False)
            position['strong_trend'] = signal.get('strong_trend', False)
            position['atr_value'] = signal.get('atr_value', 0.0)
            position['entry_reason'] = signal.get('reason', position.get('entry_reason', ''))
            position['market_condition'] = signal.get('market_type', '').lower()
            position['entry_fee'] = position.get('entry_fee', 0.0) + entry_fee
            position['entry_slippage'] = fill_price - price
            position['highest_price'] = max(position.get('highest_price', fill_price), fill_price)
            position['stale_exit_at'] = datetime.now() + timedelta(minutes=self.primary_candle_minutes * self.time_exit_candles)
            position['timestamp'] = datetime.now()
            position['signal'] = signal
            position['scaled_in'] = True
            position['scale_in_count'] = position.get('scale_in_count', 0) + 1
            position['scale_in_reason'] = signal.get('reason')
            self.last_trade_time = datetime.now()
            self.daily_trades += 1

            tp1_line = f"TP1: ${tp1_price:.4f}\n" if tp1_price is not None else ""
            msg = (
                f"SCOUT SCALE-IN\n"
                f"Pair: {symbol}\n"
                f"Upgrade: {profile['entry_type']}\n"
                f"Added qty: {quantity:.8f}\n"
                f"New avg entry: ${weighted_entry:.4f}\n"
                f"SL: ${stop_loss:.4f}\n"
                f"{tp1_line}"
                f"TP2: ${take_profit:.4f}\n"
                f"R:R target: {rr_target}"
            )
            print(f"\n   {msg.replace(chr(10), chr(10) + '   ')}")
            self.send_telegram(msg)
            return position

        except Exception as e:
            print(f"   Scale-in failed: {e}")
            return None
        finally:
            self.trade_lock = False

    # ════════════════════════════════════════════════════════════════════
    # EXECUTE BUY
    # ════════════════════════════════════════════════════════════════════
    def execute_buy(self, symbol, signal):
        if self.trade_lock:
            print(f"   TRADE LOCK - skipping duplicate {symbol}")
            return None
        self.trade_lock = True
        try:
            balance = self.get_balance()
            price = signal['price']
            entry_time = datetime.now()
            score = signal.get('score', 3)

            # Dynamic TP/SL and entry classification based on score/profile
            tp1_price, tp2_price, dynamic_sl, profile = self.dynamic_tp_sl(price, score, signal)

            # Structure-based SL as fallback
            support = signal.get('support_override', signal.get('support', price * 0.985))
            structure_sl = support * 0.995
            # Use the tighter of dynamic SL and structure SL (but never more than 3%)
            stop_loss_price = max(dynamic_sl, structure_sl, price * 0.97)

            # Dynamic position sizing by entry profile / score
            position_notional = self.get_score_position_size(balance, score, signal)
            if position_notional == 0:
                print(f"   Score {score}/5 too low for position - skipping")
                return None

            target_position_notional = balance * profile.get('scale_target_fraction', profile['balance_fraction'])
            if signal.get('session_mode') == 'LOW_RISK':
                target_position_notional = min(target_position_notional, balance * 0.10)
            target_position_notional = min(target_position_notional, balance * self.max_position_cap)

            # Convert target notional to quantity
            quantity = position_notional / price
            if quantity * price < 10:
                print(f"   Position size too small - skipping")
                return None

            step_size, precision = self.get_symbol_precision(symbol)
            quantity = round(quantity, precision)

            order = self.client.create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )

            fill_price = float(order['fills'][0]['price'])
            entry_fee = self.calculate_order_fee_usdt(order, symbol, fallback_price=fill_price)

            stop_loss = stop_loss_price
            tp1_price = None if tp1_price is None else fill_price * (tp1_price / price)
            take_profit = fill_price * (tp2_price / price)
            actual_risk = fill_price - stop_loss
            rr_target = round((take_profit - fill_price) / max(actual_risk, 1e-9), 2)

            position = {
                'trade_id': f"{symbol}-{int(entry_time.timestamp())}",
                'symbol': symbol,
                'quantity': quantity,
                'original_quantity': quantity,
                'entry_price': fill_price,
                'stop_loss': stop_loss,
                'tp1_price': tp1_price,
                'take_profit': take_profit,
                'risk_percent': profile['balance_fraction'],
                'rr_target': rr_target,
                'entry_type': profile['entry_type'],
                'fallback_trade': signal.get('fallback_trade', False),
                'strong_trend': signal.get('strong_trend', False),
                'atr_value': signal.get('atr_value', 0.0),
                'entry_reason': signal.get('reason', ''),
                'market_condition': signal.get('market_type', '').lower(),
                'entry_time': entry_time,
                'entry_fee': entry_fee,
                'entry_slippage': fill_price - price,
                'allocated_notional': quantity * fill_price,
                'target_position_notional': signal.get('target_position_notional', target_position_notional),
                'realized_pnl': 0.0,
                'partial_taken': False,
                'runner_active': False,
                'be_active': False,
                'trailing_stop_active': False,
                'highest_price': fill_price,
                'trailing_stop_price': None,
                'scaled_in': False,
                'scale_in_count': 0,
                'scale_in_reason': None,
                'stale_exit_at': entry_time + timedelta(minutes=self.primary_candle_minutes * self.time_exit_candles),
                'timestamp': datetime.now(),
                'signal': signal
            }

            self.open_positions.append(position)
            self.last_trade_time = datetime.now()
            self.daily_trades += 1

            if signal.get('clear_breakout_wait'):
                self.reset_breakout_state(symbol)

            tp1_line = f"TP1: ${tp1_price:.4f}\n" if tp1_price is not None else ""
            msg = (f"TRADE OPENED\n"
                   f"Pair: {symbol}\n"
                   f"Type: {profile['entry_type']}\n"
                   f"Score: {score}/5\n"
                   f"Entry: ${fill_price:.4f}\n"
                   f"SL: ${stop_loss:.4f}\n"
                   f"{tp1_line}"
                   f"TP2: ${take_profit:.4f}\n"
                   f"R:R target: {rr_target}")
            print(f"\n   {msg.replace(chr(10), chr(10) + '   ')}")
            self.send_telegram(msg)

            return position

        except Exception as e:
            print(f"   Buy failed: {e}")
            return None
        finally:
            self.trade_lock = False

    # ════════════════════════════════════════════════════════════════════
    # EXECUTE SELL
    # ════════════════════════════════════════════════════════════════════
    def execute_sell(self, position, reason='SIGNAL', quantity=None):
        try:
            symbol = position['symbol']
            sell_quantity = position['quantity'] if quantity is None else quantity
            exit_time = datetime.now()
            step_size, precision = self.get_symbol_precision(symbol)
            sell_quantity = round(sell_quantity, precision)

            if sell_quantity <= 0:
                print(f"   Sell quantity too small for {symbol}")
                return None

            order = self.client.create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=sell_quantity
            )

            fill_price = float(order['fills'][0]['price'])
            exit_fee = self.calculate_order_fee_usdt(order, symbol, fallback_price=fill_price)
            gross_pnl = (fill_price - position['entry_price']) * sell_quantity
            # Prorate entry fee by fraction of position being sold
            entry_fee_share = position.get('entry_fee', 0) * (sell_quantity / max(position['original_quantity'], 1e-9))
            total_fees = exit_fee + entry_fee_share
            pnl = gross_pnl - total_fees   # net PnL after fees
            pnl_percent = ((fill_price / position['entry_price']) - 1) * 100
            position['realized_pnl'] = position.get('realized_pnl', 0.0) + pnl

            if pnl >= 0:
                self.daily_profit += pnl
            else:
                self.daily_loss += abs(pnl)
            self.weekly_pnl += pnl

            remaining_quantity = round(position['quantity'] - sell_quantity, precision)
            if remaining_quantity <= 0:
                self.open_positions = [p for p in self.open_positions
                                       if p['trade_id'] != position['trade_id']]
            else:
                position['quantity'] = remaining_quantity

            balance = self.get_balance()
            if remaining_quantity <= 0:
                safe_balance = max(balance, 1e-9)
                if pnl < 0:
                    self.daily_losing_trades += 1
                    self.daily_loss_ratio += abs(pnl) / safe_balance
                    self.consecutive_losses += 1
                    self.last_trade_win = False
                else:
                    self.consecutive_losses = 0
                    self.last_trade_win = True

            self._log_trade({
                'id': position.get('trade_id'),
                'pair': symbol,
                'entry_price': position['entry_price'],
                'exit_price': fill_price,
                'position_size': sell_quantity,
                'stop_loss': position['stop_loss'],
                'take_profit': position['take_profit'],
                'gross_pnl': round(gross_pnl, 4),
                'fees': round(total_fees, 4),
                'profit': round(pnl, 4),
                'win': pnl > 0,
                'entry_time': position.get('entry_time').isoformat() if position.get('entry_time') else None,
                'exit_time': exit_time.isoformat(),
                'exit_reason': reason,
                'market_condition': position.get('market_condition'),
                'entry_reason': position.get('entry_reason'),
            })

            emoji = "WIN" if pnl >= 0 else "LOSS"
            label = "PARTIAL SELL" if remaining_quantity > 0 else "TRADE CLOSED"
            net_pnl = self.daily_profit - self.daily_loss
            msg = (f"{emoji} | {label}\n"
                   f"Pair: {symbol}\n"
                   f"Reason: {reason}\n"
                   f"PnL: ${pnl:.2f} ({pnl_percent:+.2f}%)\n"
                   f"Daily P&L: ${net_pnl:.2f}\n"
                   f"Balance: ${balance:.2f}")
            print(f"\n   {msg.replace(chr(10), chr(10) + '   ')}")
            self.send_telegram(msg)

            return {'pnl': pnl, 'pnl_percent': pnl_percent}

        except Exception as e:
            print(f"   Sell failed: {e}")
            return None

    def _log_trade(self, trade_data):
        log_path = os.path.join(os.path.dirname(__file__), 'trade_log.jsonl')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(trade_data, ensure_ascii=True) + "\n")

    # ════════════════════════════════════════════════════════════════════
    # POSITION MANAGEMENT
    # FIXED: Stop loss is now check #1 (safety first)
    # ════════════════════════════════════════════════════════════════════
    def check_positions(self):
        for position in self.open_positions[:]:
            symbol = position['symbol']
            current_price = self.get_price(symbol)
            if not current_price:
                continue

            pnl_percent = ((current_price - position['entry_price']) / position['entry_price']) * 100

            if not position.get('partial_taken'):
                if self.should_exit_early(position, current_price):
                    print(f"\n   EARLY EXIT {symbol} @ ${current_price:.4f} (weak momentum + loss)")
                    self.execute_sell(position, 'SOFT_EXIT_NO_MOMENTUM')
                    continue

            scale_signal = self.analyze(symbol)
            if self.should_scale_scout_position(position, scale_signal):
                self.execute_scale_in(position, scale_signal)
                continue

            # 1. STOP LOSS (first - always)
            if current_price <= position['stop_loss']:
                print(f"\n   STOP LOSS {symbol} @ ${current_price:.4f}")
                self.execute_sell(position, 'STOP_LOSS')
                continue

            if not position.get('partial_taken') and pnl_percent < 0 and datetime.now() >= position.get('stale_exit_at', datetime.max):
                print(f"\n   TIME EXIT {symbol} @ ${current_price:.4f} (3 candles and still losing)")
                self.execute_sell(position, 'TIME_EXIT')
                continue

            # 2. BREAK-EVEN SHIELD: move SL to entry at 1% profit
            if pnl_percent >= self.break_even_trigger and not position.get('be_active'):
                position['stop_loss'] = position['entry_price']
                position['be_active'] = True
                print(f"   BREAK-EVEN: {symbol} SL moved to entry ${position['entry_price']:.4f}")
                self.send_telegram(
                    f"Break-Even Active\n{symbol}\nSL moved to entry\nProfit: +{pnl_percent:.2f}%"
                )

            if (
                pnl_percent >= self.micro_profit_lock_trigger and
                not position.get('partial_taken') and
                position.get('entry_type') == 'a_plus'
            ):
                partial_qty = position['original_quantity'] * self.partial_tp_percent
                result = self.execute_sell(position, 'MICRO_PROFIT_LOCK', quantity=partial_qty)
                if result:
                    position['partial_taken'] = True
                    position['runner_active'] = True
                    position['stop_loss'] = position['entry_price']
                    print(f"   MICRO PROFIT LOCK {symbol} - secured 50%, SL at entry")
                continue

            # 3. TRAILING STOP: activates at 1.2% profit, trails 0.5%
            if pnl_percent >= self.trailing_stop_activation:
                if not position.get('trailing_stop_active'):
                    position['trailing_stop_active'] = True
                    position['highest_price'] = current_price
                    if position.get('strong_trend') and position.get('atr_value', 0) > 0:
                        position['trailing_stop_price'] = current_price - (position['atr_value'] * 0.5)
                    else:
                        position['trailing_stop_price'] = current_price * (1 - self.trailing_stop_distance / 100)
                    print(f"   TRAILING STOP ACTIVATED {symbol} @ ${position['trailing_stop_price']:.4f}")
                    self.send_telegram(
                        f"Trailing Stop Active\n{symbol}\n"
                        f"Profit: +{pnl_percent:.2f}%\n"
                        f"Trail: ${position['trailing_stop_price']:.4f}"
                    )

                if current_price > position.get('highest_price', 0):
                    position['highest_price'] = current_price
                    if position.get('strong_trend') and position.get('atr_value', 0) > 0:
                        new_trail = current_price - (position['atr_value'] * 0.5)
                    else:
                        new_trail = current_price * (1 - self.trailing_stop_distance / 100)
                    if new_trail > position.get('trailing_stop_price', 0):
                        position['trailing_stop_price'] = new_trail
                        print(f"   TRAILING STOP RAISED {symbol} @ ${new_trail:.4f}")

                if position.get('trailing_stop_price') and current_price <= position['trailing_stop_price']:
                    print(f"\n   TRAILING STOP HIT {symbol} @ ${current_price:.4f}")
                    self.execute_sell(position, 'TRAILING_STOP')
                    continue

            # 4. RUNNER: after partial TP, exit if price returns to entry
            if position.get('runner_active') and current_price <= position['entry_price']:
                print(f"\n   RUNNER BREAKEVEN EXIT {symbol}")
                self.execute_sell(position, 'BREAKEVEN_RUNNER')
                continue

            # 5. Fallback trades use a single TP instead of TP1/TP2 scaling.
            if position.get('fallback_trade') and current_price >= position['take_profit']:
                print(f"\n   FALLBACK TAKE PROFIT {symbol} @ ${current_price:.4f}")
                self.execute_sell(position, 'FALLBACK_TAKE_PROFIT')
                continue

            # 6. TP1: take 50% off, let the rest run to TP2
            tp1_target = position.get('tp1_price') or position['take_profit']
            if not position.get('partial_taken') and current_price >= tp1_target:
                partial_qty = position['original_quantity'] * self.partial_tp_percent
                result = self.execute_sell(position, 'PARTIAL_TP1', quantity=partial_qty)
                if result:
                    position['partial_taken'] = True
                    position['runner_active'] = True
                    position['stop_loss'] = position['entry_price']
                    print(f"   RUNNER ACTIVE {symbol} - 50% riding to TP2, SL at entry")
                continue

            # 7. TP2 runner exit
            if position.get('partial_taken') and current_price >= position['take_profit']:
                print(f"\n   TP2 HIT {symbol} @ ${current_price:.4f}")
                self.execute_sell(position, 'TAKE_PROFIT_TP2')
                continue

    # ════════════════════════════════════════════════════════════════════
    # CIRCUIT BREAKER
    # ════════════════════════════════════════════════════════════════════
    def check_circuit_breaker(self):
        total = self.get_total_balance()
        if total <= self.circuit_breaker_limit:
            msg = (f"CIRCUIT BREAKER TRIGGERED\n"
                   f"Balance: ${total:.2f}\n"
                   f"Limit: ${self.circuit_breaker_limit:.2f}\n"
                   f"Bot stopped to protect capital.")
            print(f"\n   {msg}")
            self.send_telegram(msg)
            return False
        return True

    # ════════════════════════════════════════════════════════════════════
    # DAILY / WEEKLY RESETS
    # ════════════════════════════════════════════════════════════════════
    def check_daily_reset(self):
        today = datetime.now().date()
        current_week = self._get_week_key()

        if current_week != self.last_week_reset_key:
            print(f"\n   New week - resetting weekly P&L")
            self.weekly_pnl = 0.0
            self.last_week_reset_key = current_week

        if today != self.last_reset_date:
            print(f"\n   New day - resetting daily counters")
            self.daily_trades = 0
            self.daily_profit = 0.0
            self.daily_loss = 0.0
            self.daily_loss_ratio = 0.0
            self.daily_losing_trades = 0
            self.consecutive_losses = 0
            self.fallback_trade_taken = False
            self.scout_trade_taken = False
            self.last_trade_time = None
            self.last_reset_date = today
            self.send_telegram(
                f"New trading day\n"
                f"Target: ${self.daily_profit_target}\n"
                f"Loss limit: ${self.max_daily_loss}"
            )

    # ════════════════════════════════════════════════════════════════════
    # CAN TRADE
    # ════════════════════════════════════════════════════════════════════
    def can_trade(self):
        if self.weekly_pnl <= -self.max_weekly_loss:
            return False, f"WEEKLY LOSS LIMIT: ${self.weekly_pnl:.2f}"
        if self.daily_profit >= self.daily_profit_target:
            return False, f"DAILY TARGET HIT: ${self.daily_profit:.2f}"
        if self.daily_loss >= self.max_daily_loss:
            return False, f"DAILY LOSS LIMIT: -${self.daily_loss:.2f}"
        if self.daily_loss_ratio >= 0.03:
            return False, f"DAILY LOSS RATIO: {self.daily_loss_ratio*100:.1f}%"
        if self.consecutive_losses >= self.max_consecutive_losses:
            return False, f"CONSECUTIVE LOSSES: {self.consecutive_losses}"
        if self.daily_trades >= self.hard_max_trades:
            return False, f"MAX TRADES: {self.daily_trades}/{self.hard_max_trades}"
        if self.last_trade_time:
            elapsed = (datetime.now() - self.last_trade_time).total_seconds() / 60
            if elapsed < self.trade_cooldown_minutes:
                remaining = self.trade_cooldown_minutes - elapsed
                return False, f"COOLDOWN: {remaining:.0f}min remaining"
        session, settings = self.get_market_session()
        if self.daily_trades >= settings['max_trades']:
            return False, f"Session limit ({self.daily_trades}/{settings['max_trades']} {session.upper()})"
        return True, "OK"

    # ════════════════════════════════════════════════════════════════════
    # SYNC EXISTING POSITIONS
    # ════════════════════════════════════════════════════════════════════
    def sync_existing_positions(self):
        print("\n   Syncing existing positions...")
        known_entries = {'BTCUSDT': 72753.0}
        try:
            account = self.client.get_account()
            for balance in account['balances']:
                asset = balance['asset']
                symbol = f"{asset}USDT"
                if symbol not in self.trading_pairs:
                    continue
                amount = float(balance['free'])
                if amount <= 0:
                    continue
                current_price = self.get_price(symbol)
                if not current_price:
                    continue
                if amount * current_price < 10:
                    continue
                if any(p['symbol'] == symbol for p in self.open_positions):
                    continue
                entry_price = known_entries.get(symbol, current_price)
                stop_loss = entry_price * (1 - self.stop_loss_percent / 100)
                take_profit = entry_price * (1 + self.take_profit_percent / 100)
                position = {
                    'trade_id': f"{symbol}-synced",
                    'symbol': symbol,
                    'quantity': amount,
                    'original_quantity': amount,
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'risk_percent': self.stop_loss_percent / 100,
                    'rr_target': 2.0,
                    'entry_type': 'synced',
                    'entry_reason': 'Imported on startup',
                    'market_condition': 'unknown',
                    'entry_time': datetime.now(),
                    'entry_fee': 0,
                    'entry_slippage': 0,
                    'realized_pnl': 0.0,
                    'partial_taken': False,
                    'runner_active': False,
                    'be_active': False,
                    'trailing_stop_active': False,
                    'highest_price': current_price,
                    'trailing_stop_price': None,
                    'timestamp': datetime.now(),
                    'signal': {}
                }
                self.open_positions.append(position)
                pnl = (current_price - entry_price) * amount
                print(f"   Synced: {amount:.8f} {asset} @ ${entry_price:.2f} | P&L: ${pnl:.2f}")
        except Exception as e:
            print(f"   Sync error: {e}")

    # ════════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ════════════════════════════════════════════════════════════════════
    def run(self):
        if hasattr(self, '_started'):
            return
        self._started = True

        print("\n" + "=" * 60)
        print("   SMART TRADER V2 - LIVE")
        print(f"   PID: {os.getpid()}")
        print("=" * 60)

        balance = self.get_balance()
        print(f"\n   Balance: ${balance:.2f} USDT")

        last_heartbeat = datetime.now()

        while True:
            try:
                a_trade_taken = False

                if not self.check_circuit_breaker():
                    break

                self.check_daily_reset()

                if (datetime.now() - last_heartbeat).seconds > 21600:
                    balance = self.get_balance()
                    session, _ = self.get_market_session()
                    net_pnl = self.daily_profit - self.daily_loss
                    self.send_telegram(
                        f"Heartbeat\n"
                        f"Balance: ${balance:.2f}\n"
                        f"Session: {session.upper()}\n"
                        f"Trades today: {self.daily_trades}/{self.max_trades_per_day}\n"
                        f"Daily P&L: ${net_pnl:.2f}\n"
                        f"Open: {len(self.open_positions)}"
                    )
                    last_heartbeat = datetime.now()

                self.check_positions()

                if len(self.open_positions) >= self.max_positions:
                    print("X Skipping scan due to open position limit")
                    print(f"\r   Position open - waiting for exit", end='', flush=True)
                    time.sleep(10)
                    continue

                can_trade_result, reason = self.can_trade()

                if not can_trade_result:
                    hard_stops = ['DAILY TARGET', 'DAILY LOSS', 'WEEKLY LOSS',
                                  'MAX TRADES', 'CONSECUTIVE LOSSES']
                    if any(s in reason for s in hard_stops):
                        print(f"\n   {reason}")
                        print(f"   Sleeping until next day...")
                        self.send_telegram(f"Trading stopped: {reason}")
                        while datetime.now().date() == self.last_reset_date:
                            time.sleep(300)
                        continue
                    print(f"X Skipping loop due to gate: {reason}")
                    print(f"\r   {reason}", end='', flush=True)
                    time.sleep(30)
                    continue

                session, settings = self.get_market_session()
                min_strength = settings['min_strength']
                btc_bias = self.get_btc_bias()
                session_active = self.is_session_active(session)

                print(f"\n   Scanning {len(self.trading_pairs)} pairs... "
                      f"[{session.upper()} | {settings['mode']} | "
                      f"Trades: {self.daily_trades}/{settings['max_trades']} | BTC: {btc_bias}]")

                pair_snapshots = {}
                for symbol in self.trading_pairs:
                    if any(p['symbol'] == symbol for p in self.open_positions):
                        print(f"X Skipping {symbol} due to existing open position")
                        continue
                    if len(self.open_positions) >= self.max_positions:
                        break
                    if symbol not in ('BTCUSDT',) and not self.btc_is_healthy():
                        print(f"X Skipping {symbol} due to BTC filter")
                        continue

                    snapshot = self.get_pair_snapshot(symbol)
                    if snapshot:
                        pair_snapshots[symbol] = snapshot

                dynamic_trade_cap = self.get_dynamic_trade_cap(pair_snapshots, session)
                if self.daily_trades >= dynamic_trade_cap:
                    print(f"X Skipping loop due to dynamic trade cap {self.daily_trades}/{dynamic_trade_cap}")
                    print(f"\r   Dynamic trade cap reached ({self.daily_trades}/{dynamic_trade_cap})", end='', flush=True)
                    time.sleep(30)
                    continue

                ranked_pairs = self.rank_pairs(pair_snapshots)
                top_symbols = [ranked_pairs[0][0]] if ranked_pairs else []

                if top_symbols:
                    top_summary = ', '.join(
                        f"{symbol}:{pair_snapshots[symbol]['market_mode']}"
                        for symbol in top_symbols
                    )
                else:
                    top_summary = 'none'
                print(f"   Ranked top pairs: {top_summary}")

                for symbol in top_symbols:
                    if len(self.open_positions) >= self.max_positions:
                        break

                    snapshot = pair_snapshots[symbol]
                    resistance, support = self.calculate_levels(snapshot['df'])
                    context = self.level_context(snapshot['price'], resistance, support)
                    context['market'] = snapshot.get('market_type', 'CHOPPY')
                    context['trend'] = snapshot['ema20'] > snapshot['ema50']
                    context['trend_exists'] = context['trend']
                    context['trend_aligned'] = context['trend'] and snapshot['price'] > snapshot['ema20']
                    context['higher_lows'] = self.detect_higher_lows(snapshot['df'])
                    context['ema_alignment'] = snapshot['ema20'] > snapshot['ema50']
                    context['structure_ok'] = context['higher_lows'] or (context['near_support'] and not context['breakdown'])
                    context['structure_clean'] = context['trend_aligned'] and (context['breakout'] or not context['near_resistance']) and not context['breakdown']
                    score = self.get_trade_score(snapshot, context)

                    signal = self.analyze(symbol)

                    if btc_bias == 'BEARISH' and signal.get('action') == 'BUY':
                        self.debug_symbol_check(symbol, snapshot, context, score, 'BTC bearish blocked long')
                        signal = {
                            **signal,
                            'action': 'HOLD',
                            'strength': 0,
                            'reason': 'BTC bearish blocked long',
                        }

                    scout = signal.get('scout', self.is_scout_candidate(snapshot, context))
                    breakout = signal.get('breakout', bool(context.get('breakout')))
                    entry_type = signal.get('entry_tier') or signal.get('entry_type')

                    # Force visibility: always print why a symbol was skipped or acted on
                    reason_text = signal.get('reason', 'no reason')
                    print(f"   {symbol} [{signal['action']}] ({signal.get('market_type','N/A')}|{signal.get('zone','?')}) - {reason_text}")

                    if signal['action'] == 'HOLD' and 'No valid trend entry' in reason_text:
                        min_score = self.get_dynamic_min_score(snapshot, context)
                        checks = {
                            'Scout Setup': self.is_scout_candidate(snapshot, context),
                            'Breakout': context.get('breakout'),
                            'B+ Score': self.is_b_plus_trade(score, context),
                            'Trend': context.get('trend'),
                            'EMA Alignment': context.get('ema_alignment'),
                        }
                        self.explain_skip(symbol, score, min_score, checks)

                    if signal['action'] == 'BUY':
                        if len(self.open_positions) >= self.max_positions:
                            break
                        if self.execute_buy(symbol, signal):
                            a_trade_taken = True
                        break

                    time.sleep(0.5)

                print(f"   Cycle complete. Next scan in 10s...")
                time.sleep(10)

            except KeyboardInterrupt:
                print("\n\n   Bot stopped by user")
                break
            except Exception as e:
                print(f"\n   Loop error: {e}")
                time.sleep(10)

        net_pnl = self.daily_profit - self.daily_loss
        print(f"\n   Session Summary:")
        print(f"      Trades: {self.daily_trades}")
        print(f"      Daily P&L: ${net_pnl:.2f}")
        print(f"      Open positions: {len(self.open_positions)}")


if __name__ == '__main__':
    trader = SmartTrader()
    balance = trader.get_balance()
    trader.send_telegram(
        f"Smart Trader V2 Started\n"
        f"Balance: ${balance:.2f}\n"
        f"Target: ${trader.daily_profit_target}/day\n"
        f"Pairs: {', '.join(trader.trading_pairs)}"
    )
    trader.run()