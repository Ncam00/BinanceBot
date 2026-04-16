"""
SMART TRADER V2 - Location-Based Trading Bot
=============================================
Fixed & Cleaned: April 2026
Target: $5/day | Protect capital first

Key Features:
1. Support/Resistance detection - location-based entries only
2. No-trade zone (skip middle 40% of range)
3. Market type detection (range vs trend)
4. Strategy switch per market type
5. Max 3 trades per day
6. Daily profit lock at $5
7. Daily loss limit at $7
8. Weekly loss limit at $20
9. Circuit breaker at 5% account drawdown
10. Break-even shield at 1% profit
11. Partial TP (70%) then trailing runner
12. Trailing stop: activates at 1.5%, trails 0.8%

Pairs: BTCUSDT, ETHUSDT, SOLUSDT, AVAXUSDT, BNBUSDT
"""

import os
import time
import json
from datetime import datetime
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
            'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'AVAXUSDT', 'BNBUSDT'
        ]
        self.max_positions = 1  # One position at a time - quality over quantity

        # ════════════════════════════════════════════════════════════════════
        # CORE RISK SETTINGS
        # ════════════════════════════════════════════════════════════════════
        self.stop_loss_percent = 1.5          # 1.5% stop loss
        self.take_profit_percent = 2.5        # 2.5% take profit
        self.position_size_percent = 15       # 15% of balance per trade (~$70)
        self.max_position_cap = 0.25          # Hard cap at 25% of balance
        self.strong_setup_threshold = 0.85    # strength >= 0.85 → full size; below → half size

        # ════════════════════════════════════════════════════════════════════
        # DAILY / WEEKLY LIMITS
        # ════════════════════════════════════════════════════════════════════
        self.daily_profit_target = 5.00       # Stop new trades at $5 profit
        self.max_daily_loss = 7.00            # Stop trading at $7 loss
        self.max_weekly_loss = 20.00          # Stop trading at $20 loss this week
        self.max_trades_per_day = 3           # Absolute max trades per day
        self.hard_max_trades = 3              # Cannot be bypassed
        self.trade_cooldown_minutes = 5       # 5 min between trades
        self.max_consecutive_losses = 2       # Stop after 2 losses in a row

        # ════════════════════════════════════════════════════════════════════
        # CIRCUIT BREAKER
        # 5% drawdown from starting balance kills the bot
        # ════════════════════════════════════════════════════════════════════
        self.starting_balance = 468.35
        self.circuit_breaker_percent = 0.05
        self.circuit_breaker_limit = self.starting_balance * (1 - self.circuit_breaker_percent)

        # ════════════════════════════════════════════════════════════════════
        # EXIT MANAGEMENT
        # ════════════════════════════════════════════════════════════════════
        self.break_even_trigger = 1.0         # Move SL to entry at 1% profit
        self.trailing_stop_activation = 1.5   # Activate trailing stop at 1.5% profit
        self.trailing_stop_distance = 0.8     # Trail 0.8% below peak
        self.partial_tp_percent = 0.70        # Sell 70% at first TP, let 30% run

        # ════════════════════════════════════════════════════════════════════
        # LOCATION-BASED TRADING SETTINGS
        # ════════════════════════════════════════════════════════════════════
        self.sr_lookback = 50                 # Candles for S/R detection
        self.no_trade_zone_percent = 40       # Skip middle 40% of range
        self.near_level_percent = 1.5         # Within 1.5% of S/R level

        # ════════════════════════════════════════════════════════════════════
        # ADX THRESHOLDS
        # ════════════════════════════════════════════════════════════════════
        self.adx_range_threshold = 20         # ADX < 20 = ranging market
        self.adx_trend_threshold = 25         # ADX > 25 = trending market
        self.min_atr_percent = 0.003          # Skip trades when ATR < 0.3% of price
        self.max_spread_percent = 0.001       # Skip trades when spread > 0.1% of price

        # ════════════════════════════════════════════════════════════════════
        # SESSION SETTINGS (NZ TIME)
        # Asia:   11:00-19:00 NZT - slow, max 1 trade
        # London: 19:00-03:00 NZT - normal, max 3 trades
        # US:     03:00-11:00 NZT - best volatility, max 3 trades
        # ════════════════════════════════════════════════════════════════════
        self.nz_timezone = pytz.timezone('Pacific/Auckland')
        self.session_settings = {
            'asia':   {'mode': 'low_risk',   'max_trades': 1, 'min_strength': 0.85},
            'london': {'mode': 'normal',     'max_trades': 3, 'min_strength': 0.75},
            'us':     {'mode': 'aggressive', 'max_trades': 3, 'min_strength': 0.70},
        }

        # ════════════════════════════════════════════════════════════════════
        # STATE TRACKING (single source of truth)
        # ════════════════════════════════════════════════════════════════════
        self.daily_profit = 0.0               # SINGLE profit tracker
        self.daily_loss = 0.0                 # SINGLE loss tracker
        self.daily_loss_ratio = 0.0
        self.weekly_pnl = 0.0
        self.daily_trades = 0
        self.consecutive_losses = 0
        self.open_positions = []
        self.symbol_state = {}
        self.trade_lock = False
        self.last_trade_time = None
        self.last_reset_date = datetime.now().date()
        self.last_week_reset_key = self._get_week_key()

        # Telegram
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')

        print("=" * 60)
        print("   🚀 SMART TRADER V2 - INITIALIZING")
        print("=" * 60)
        print(f"   Pairs:          {', '.join(self.trading_pairs)}")
        print(f"   Max trades/day: {self.max_trades_per_day}")
        print(f"   Daily target:   ${self.daily_profit_target}")
        print(f"   Daily loss cap: ${self.max_daily_loss}")
        print(f"   Weekly loss cap:${self.max_weekly_loss}")
        print(f"   Circuit breaker:${self.circuit_breaker_limit:.2f}")
        print(f"   Position size:  {self.position_size_percent}%")
        print(f"   SL: {self.stop_loss_percent}% | TP: {self.take_profit_percent}%")
        session, settings = self.get_market_session()
        print(f"   Session:        {session.upper()} ({settings['mode']})")
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
            print(f"   ❌ Candle fetch error {symbol}: {e}")
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
            print(f"   ❌ Balance error: {e}")
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
            'minus_di': minus_di.iloc[-1] if not np.isnan(minus_di.iloc[-1]) else 0,
            'atr': atr.iloc[-1] if not np.isnan(atr.iloc[-1]) else 0
        }

    def calculate_bollinger(self, closes, period=20, std_dev=2):
        sma = closes.rolling(window=period).mean()
        std = closes.rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        current_price = closes.iloc[-1]
        bb_range = upper.iloc[-1] - lower.iloc[-1]
        pb = (current_price - lower.iloc[-1]) / bb_range if bb_range > 0 else 0.5
        return {
            'upper': upper.iloc[-1],
            'middle': sma.iloc[-1],
            'lower': lower.iloc[-1],
            'pb': pb
        }

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
        if adx_value < self.adx_range_threshold:
            return 'RANGE'
        elif adx_value >= self.adx_trend_threshold:
            return 'TREND'
        return 'MIXED'

    def is_uptrend(self, price, ma):
        return price > ma

    def is_downtrend(self, price, ma):
        return price < ma

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

    def get_trend_signal(self, price, rsi, macd, ema_fast, ema_slow, adx, support, resistance):
        near_support = self.is_near_level(price, support)
        macd_bullish = macd['macd'] > macd['signal'] and macd['histogram'] > macd['prev_histogram']
        macd_bearish = macd['macd'] < macd['signal'] and macd['histogram'] < macd['prev_histogram']
        ema_bullish = ema_fast > ema_slow
        trend_up = adx['plus_di'] > adx['minus_di']

        if near_support and trend_up and macd_bullish and ema_bullish and rsi < 50:
            return {
                'action': 'BUY',
                'strength': 0.85,
                'reason': f"TREND BUY: Pullback to support (ADX={adx['adx']:.1f})",
                'entry_type': 'PULLBACK'
            }
        if price > resistance and trend_up and macd_bullish and ema_bullish:
            return {
                'action': 'BUY',
                'strength': 0.75,
                'reason': "TREND BUY: Breakout above resistance",
                'entry_type': 'BREAKOUT'
            }
        if price < support and not trend_up and macd_bearish:
            return {
                'action': 'SELL',
                'strength': 0.85,
                'reason': "TREND SELL: Breakdown below support"
            }
        return {'action': 'HOLD', 'strength': 0, 'reason': 'Trend: No clear setup'}

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
                print(f"   ⚠️ BTC FILTER: BTC dropping ({change_1h:.2f}% 1h) - blocking alts")
                return False
            return True
        except Exception as e:
            print(f"   ⚠️ BTC filter error: {e}")
            return True

    def check_spread(self, symbol):
        try:
            book = self.client.get_order_book(symbol=symbol, limit=5)
            bid = float(book['bids'][0][0])
            ask = float(book['asks'][0][0])
            mid = (bid + ask) / 2
            if mid == 0:
                return True
            spread_pct = (ask - bid) / mid
            if spread_pct > self.max_spread_percent:
                print(f"   📊 WIDE SPREAD: {spread_pct*100:.3f}% - skipping")
                return False
            return True
        except Exception as e:
            print(f"   ⚠️ Spread check error: {e}")
            return True

    def check_volume(self, df):
        volumes = df['volume'].tolist()
        if len(volumes) < 20:
            return True
        current_volume = volumes[-1]
        avg_volume = sum(volumes[-20:-1]) / 19
        ratio = current_volume / avg_volume if avg_volume > 0 else 1
        if ratio < 1.0:
            print(f"   📉 VOLUME BELOW AVG: {ratio:.2f}x - skipping")
            return False
        return True

    def get_trend(self, symbol, timeframe):
        df = self.get_candles(symbol, timeframe, 50)
        if df is None:
            return 'NEUTRAL'
        closes = df['close']
        ema7 = closes.ewm(span=7).mean().iloc[-1]
        ema18 = closes.ewm(span=18).mean().iloc[-1]
        if closes.iloc[-1] > ema7 and ema7 > ema18:
            return 'BULL'
        if closes.iloc[-1] < ema7 and ema7 < ema18:
            return 'BEAR'
        return 'NEUTRAL'

    def check_multi_timeframe(self, symbol):
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
        if bullish_count < 2:
            print(f"   ⏱️ MTF: Only {bullish_count}/3 timeframes bullish - blocking")
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
                'signal_active': False,
                'signal_level': None,
            }
        return self.symbol_state[symbol]

    def reset_breakout_state(self, symbol):
        self.symbol_state[symbol] = {
            'waiting_for_retest': False,
            'breakout_level': None,
            'breakout_direction': None,
            'retest_candles': 0,
            'signal_active': False,
            'signal_level': None,
        }

    # ════════════════════════════════════════════════════════════════════
    # MAIN ANALYSIS (LOCATION-BASED)
    # ════════════════════════════════════════════════════════════════════
    def analyze(self, symbol):
        # Stop scanning for new trades if daily target hit
        if self.daily_profit >= self.daily_profit_target:
            return {'action': 'HOLD', 'strength': 0,
                    'reason': f'Daily target ${self.daily_profit_target} hit - no new trades'}

        df = self.get_candles(symbol, '15m', 100)
        if df is None or len(df) < 50:
            return {'action': 'HOLD', 'strength': 0, 'reason': 'Insufficient data'}

        closes = df['close']
        price = closes.iloc[-1]

        # Indicators
        rsi = self.calculate_rsi(closes)
        macd = self.calculate_macd(closes)
        ema_fast = self.calculate_ema(closes, 7)
        ema_slow = self.calculate_ema(closes, 18)
        ema_trend = self.calculate_ema(closes, 50)
        adx = self.calculate_adx(df)
        bb = self.calculate_bollinger(closes)

        # Support/Resistance
        sr = self.calculate_support_resistance(df)
        support = sr['support']
        resistance = sr['resistance']

        market_type = self.get_market_type(adx['adx'])
        state = self.get_symbol_state(symbol)
        tolerance = 0.002

        # ── Breakout state machine ────────────────────────────────────
        if market_type == 'TREND' and price > resistance and not state['waiting_for_retest']:
            state['waiting_for_retest'] = True
            state['breakout_level'] = resistance
            state['breakout_direction'] = 'LONG'
            state['retest_candles'] = 0
            state['signal_active'] = True
            state['signal_level'] = resistance
            self.send_telegram(
                f"📈 {symbol} Breakout detected\n"
                f"Level: ${resistance:.4f}\nWaiting for retest..."
            )
            return {
                'action': 'HOLD', 'strength': 0,
                'reason': f"⏳ Breakout at ${resistance:.4f} - waiting for retest",
                'market_type': market_type, 'price': price,
                'support': support, 'resistance': resistance,
                'rsi': rsi, 'adx': adx['adx'], 'zone': 'breakout_wait'
            }

        if state.get('signal_active'):
            state['retest_candles'] += 1
            if state['retest_candles'] > 10:
                self.reset_breakout_state(symbol)
                return {
                    'action': 'HOLD', 'strength': 0,
                    'reason': '⏳ Breakout retest expired (10 candles)',
                    'market_type': market_type, 'price': price,
                    'support': support, 'resistance': resistance,
                    'rsi': rsi, 'adx': adx['adx'], 'zone': 'breakout_timeout'
                }

            retest_hit = (
                state['breakout_direction'] == 'LONG' and
                price <= state['signal_level'] * (1 + tolerance)
            )
            if retest_hit:
                current_open = df['open'].iloc[-1]
                current_close = df['close'].iloc[-1]
                if current_close > current_open and rsi > 50:
                    state['signal_active'] = False
                    signal = {
                        'action': 'BUY', 'strength': 0.80,
                        'reason': f"BREAKOUT BUY: Retest confirmed @ ${state['signal_level']:.4f}",
                        'entry_type': 'BREAKOUT',
                        'support_override': state['signal_level'],
                        'clear_breakout_wait': True
                    }
                else:
                    return {
                        'action': 'HOLD', 'strength': 0,
                        'reason': '⏳ Retest touched - waiting for confirmation candle',
                        'market_type': market_type, 'price': price,
                        'support': support, 'resistance': resistance,
                        'rsi': rsi, 'adx': adx['adx'], 'zone': 'breakout_retest'
                    }
            else:
                return {
                    'action': 'HOLD', 'strength': 0,
                    'reason': f"⏳ Watching retest at ${state['signal_level']:.4f}",
                    'market_type': market_type, 'price': price,
                    'support': support, 'resistance': resistance,
                    'rsi': rsi, 'adx': adx['adx'], 'zone': 'breakout_wait'
                }

        # ── HARD BLOCK: must be near S/R ─────────────────────────────
        near_support = self.is_near_level(price, support)
        near_resistance = self.is_near_level(price, resistance)

        if not near_support and not near_resistance:
            return {
                'action': 'HOLD', 'strength': 0,
                'reason': '🚫 HARD BLOCK: Not at support/resistance',
                'market_type': market_type, 'price': price,
                'support': support, 'resistance': resistance,
                'rsi': rsi, 'adx': adx['adx'], 'zone': 'middle'
            }

        zone = self.get_trade_zone(price, support, resistance)
        if zone == 'middle':
            return {
                'action': 'HOLD', 'strength': 0,
                'reason': '🚫 HARD BLOCK: Price in middle zone',
                'market_type': market_type, 'price': price,
                'support': support, 'resistance': resistance,
                'rsi': rsi, 'adx': adx['adx'], 'zone': zone
            }

        # ── Strategy signal ───────────────────────────────────────────
        if market_type == 'RANGE':
            signal = self.get_range_signal(price, rsi, bb, support, resistance)
        elif market_type == 'TREND':
            signal = self.get_trend_signal(
                price, rsi, macd, ema_fast, ema_slow, adx, support, resistance
            )
        else:
            signal = {'action': 'HOLD', 'strength': 0,
                      'reason': 'MIXED market - waiting for clarity'}

        # ── Confirmation candle ───────────────────────────────────────
        if signal['action'] == 'BUY' and signal.get('entry_type') != 'BREAKOUT':
            if not self.has_confirmation_candle(df, 'bullish'):
                signal = {'action': 'HOLD', 'strength': 0,
                          'reason': '⏳ Buy signal - waiting for confirmation candle'}

        # ===== FILTERS =====
        if signal['action'] == 'BUY':
            # Trend filter
            if not self.is_uptrend(price, ema_trend):
                return {'action': 'HOLD', 'strength': 0,
                        'reason': f'📉 Price below EMA50 ({ema_trend:.4f}) - no longs'}
            # Volume filter
            if not self.check_volume(df):
                return {'action': 'HOLD', 'strength': 0,
                        'reason': '📉 Low volume - entry blocked'}
            # Volatility filter (ATR)
            if adx['atr'] < price * self.min_atr_percent:
                return {'action': 'HOLD', 'strength': 0,
                        'reason': f'📉 Low volatility - ATR {adx["atr"]:.4f} below threshold'}
            # Spread check
            if not self.check_spread(symbol):
                return {'action': 'HOLD', 'strength': 0,
                        'reason': '📊 Wide spread - entry blocked'}
            if not self.btc_is_healthy():
                return {'action': 'HOLD', 'strength': 0,
                        'reason': '🛡️ BTC dumping - entry blocked'}
            trend_15m = self.get_trend(symbol, '15m')
            trend_5m = self.get_trend(symbol, '5m')
            if trend_5m != trend_15m:
                return {'action': 'HOLD', 'strength': 0,
                        'reason': f'⏱️ Trend mismatch: 5m={trend_5m} 15m={trend_15m} - entry blocked'}
            if not self.check_multi_timeframe(symbol):
                return {'action': 'HOLD', 'strength': 0,
                        'reason': '⏱️ Timeframes not aligned - entry blocked'}

        # ── Clear breakout state if trade confirmed ───────────────────
        if signal.get('clear_breakout_wait'):
            self.reset_breakout_state(symbol)

        # ── Add metadata ──────────────────────────────────────────────
        signal['market_type'] = market_type
        signal['price'] = price
        signal['support'] = support
        signal['resistance'] = resistance
        signal['rsi'] = rsi
        signal['adx'] = adx['adx']
        signal['zone'] = zone

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
        # Cap at max_position_cap of balance
        max_size = (balance * self.max_position_cap) / entry_price
        position_size = min(position_size, max_size)
        if position_size * entry_price < 10:
            return 0
        return position_size

    # ════════════════════════════════════════════════════════════════════
    # EXECUTE BUY
    # ════════════════════════════════════════════════════════════════════
    def execute_buy(self, symbol, signal):
        if self.trade_lock:
            print(f"   🔒 TRADE LOCK - skipping duplicate {symbol}")
            return None
        self.trade_lock = True
        try:
            balance = self.get_balance()
            price = signal['price']
            entry_time = datetime.now()

            # Stop loss: structure-based, never more than 3%
            support = signal.get('support_override', signal.get('support', price * 0.985))
            structure_sl = support * 0.995
            max_sl = price * 0.97
            stop_loss_price = max(structure_sl, max_sl)

            # Risk % by session and setup quality
            session, _ = self.get_market_session()
            base_risk = 0.01 if session == 'asia' else 0.015
            strong_setup = signal.get('strength', 0) >= self.strong_setup_threshold
            risk_percent = base_risk if strong_setup else base_risk * 0.5
            print(f"   📐 {'STRONG' if strong_setup else 'DECENT'} setup "
                  f"(strength={signal.get('strength', 0):.2f}) → risk {risk_percent*100:.2f}%")

            quantity = self.calculate_position_size(balance, price, stop_loss_price, risk_percent)
            if quantity == 0:
                print(f"   ⚠️ Position size too small - skipping")
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

            # Take profit at 2.5% from fill price (fixed, not 1:1)
            stop_loss = stop_loss_price
            take_profit = fill_price * (1 + self.take_profit_percent / 100)
            actual_risk = fill_price - stop_loss
            rr_target = round((take_profit - fill_price) / max(actual_risk, 1e-9), 2)

            position = {
                'trade_id': f"{symbol}-{int(entry_time.timestamp())}",
                'symbol': symbol,
                'quantity': quantity,
                'original_quantity': quantity,
                'entry_price': fill_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'risk_percent': risk_percent,
                'rr_target': rr_target,
                'entry_type': signal.get('entry_type', 'PULLBACK').lower(),
                'entry_reason': signal.get('reason', ''),
                'market_condition': signal.get('market_type', '').lower(),
                'entry_time': entry_time,
                'entry_fee': entry_fee,
                'entry_slippage': fill_price - price,
                'realized_pnl': 0.0,
                'partial_taken': False,
                'runner_active': False,
                'be_active': False,
                'trailing_stop_active': False,
                'highest_price': fill_price,
                'trailing_stop_price': None,
                'timestamp': datetime.now(),
                'signal': signal
            }

            self.open_positions.append(position)
            self.last_trade_time = datetime.now()
            self.daily_trades += 1

            if signal.get('clear_breakout_wait'):
                self.reset_breakout_state(symbol)

            msg = (f"🚀 TRADE OPENED\n"
                   f"Pair: {symbol}\n"
                   f"Type: {signal.get('entry_type', 'PULLBACK')}\n"
                   f"Entry: ${fill_price:.4f}\n"
                   f"SL: ${stop_loss:.4f} ({self.stop_loss_percent}%)\n"
                   f"TP: ${take_profit:.4f} ({self.take_profit_percent}%)\n"
                   f"R:R target: {rr_target}")
            print(f"\n   {msg.replace(chr(10), chr(10) + '   ')}")
            self.send_telegram(msg)

            return position

        except Exception as e:
            print(f"   ❌ Buy failed: {e}")
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
                print(f"   ⚠️ Sell quantity too small for {symbol}")
                return None

            order = self.client.create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=sell_quantity
            )

            fill_price = float(order['fills'][0]['price'])
            exit_fee = self.calculate_order_fee_usdt(order, symbol, fallback_price=fill_price)
            pnl = (fill_price - position['entry_price']) * sell_quantity
            pnl_percent = ((fill_price / position['entry_price']) - 1) * 100
            total_trade_pnl = position.get('realized_pnl', 0.0) + pnl
            position['realized_pnl'] = total_trade_pnl

            # Update single profit/loss tracker
            if pnl >= 0:
                self.daily_profit += pnl
            else:
                self.daily_loss += abs(pnl)
            self.weekly_pnl += pnl

            # Remove or reduce position
            remaining_quantity = round(position['quantity'] - sell_quantity, precision)
            if remaining_quantity <= 0:
                self.open_positions = [p for p in self.open_positions
                                       if p['trade_id'] != position['trade_id']]
            else:
                position['quantity'] = remaining_quantity

            # Risk controls
            balance = self.get_balance()
            if remaining_quantity <= 0:
                safe_balance = max(balance, 1e-9)
                if pnl < 0:
                    self.daily_loss_ratio += abs(pnl) / safe_balance
                    self.consecutive_losses += 1
                else:
                    self.consecutive_losses = 0

            # Log trade
            self._log_trade({
                'id': position.get('trade_id'),
                'pair': symbol,
                'entry_price': position['entry_price'],
                'exit_price': fill_price,
                'position_size': sell_quantity,
                'stop_loss': position['stop_loss'],
                'take_profit': position['take_profit'],
                'profit': round(pnl, 4),
                'win': pnl > 0,
                'entry_time': position.get('entry_time').isoformat() if position.get('entry_time') else None,
                'exit_time': exit_time.isoformat(),
                'exit_reason': reason,
                'market_condition': position.get('market_condition'),
                'entry_reason': position.get('entry_reason'),
            })

            emoji = "✅" if pnl >= 0 else "❌"
            label = "PARTIAL SELL" if remaining_quantity > 0 else "TRADE CLOSED"
            msg = (f"{emoji} {label}\n"
                   f"Pair: {symbol}\n"
                   f"Reason: {reason}\n"
                   f"PnL: ${pnl:.2f} ({pnl_percent:+.2f}%)\n"
                   f"Daily P&L: ${self.daily_profit - self.daily_loss:.2f}\n"
                   f"Balance: ${balance:.2f}")
            print(f"\n   {msg.replace(chr(10), chr(10) + '   ')}")
            self.send_telegram(msg)

            return {'pnl': pnl, 'pnl_percent': pnl_percent}

        except Exception as e:
            print(f"   ❌ Sell failed: {e}")
            return None

    def _log_trade(self, trade_data):
        log_path = os.path.join(os.path.dirname(__file__), 'trade_log.jsonl')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(trade_data, ensure_ascii=True) + "\n")

    # ════════════════════════════════════════════════════════════════════
    # POSITION MANAGEMENT (single unified exit system)
    # ════════════════════════════════════════════════════════════════════
    def check_positions(self):
        for position in self.open_positions[:]:
            symbol = position['symbol']
            current_price = self.get_price(symbol)
            if not current_price:
                continue

            pnl_percent = ((current_price - position['entry_price']) / position['entry_price']) * 100

            # 1. STOP LOSS
            if current_price <= position['stop_loss']:
                print(f"\n   🛑 STOP LOSS {symbol} @ ${current_price:.4f}")
                self.execute_sell(position, 'STOP_LOSS')
                continue

            # 2. BREAK-EVEN SHIELD: move SL to entry at 1% profit
            if pnl_percent >= self.break_even_trigger and not position.get('be_active'):
                position['stop_loss'] = position['entry_price']
                position['be_active'] = True
                print(f"   🛡️ BREAK-EVEN: {symbol} SL moved to entry ${position['entry_price']:.4f}")
                self.send_telegram(
                    f"🛡️ Break-Even Active\n{symbol}\nSL moved to entry"
                )

            # 3. TRAILING STOP: activates at 1.5% profit, trails 0.8%
            if pnl_percent >= self.trailing_stop_activation:
                if not position.get('trailing_stop_active'):
                    position['trailing_stop_active'] = True
                    position['highest_price'] = current_price
                    position['trailing_stop_price'] = current_price * (1 - self.trailing_stop_distance / 100)
                    print(f"   🔒 TRAILING STOP ACTIVATED {symbol} @ ${position['trailing_stop_price']:.4f}")
                    self.send_telegram(
                        f"🔒 Trailing Stop Active\n{symbol}\n"
                        f"Profit: +{pnl_percent:.2f}%\n"
                        f"Trail: ${position['trailing_stop_price']:.4f}"
                    )

                # Update trailing stop if price moves higher
                if current_price > position.get('highest_price', 0):
                    position['highest_price'] = current_price
                    new_trail = current_price * (1 - self.trailing_stop_distance / 100)
                    if new_trail > position.get('trailing_stop_price', 0):
                        position['trailing_stop_price'] = new_trail
                        print(f"   📈 TRAILING STOP RAISED {symbol} @ ${new_trail:.4f}")

                # Check if trailing stop hit
                if position.get('trailing_stop_price') and current_price <= position['trailing_stop_price']:
                    print(f"\n   🔒 TRAILING STOP HIT {symbol} @ ${current_price:.4f}")
                    self.execute_sell(position, 'TRAILING_STOP')
                    continue

            # 4. RUNNER: after partial TP, exit if price returns to entry
            if position.get('runner_active') and current_price <= position['entry_price']:
                print(f"\n   ⚖️ BREAKEVEN RUNNER EXIT {symbol}")
                self.execute_sell(position, 'BREAKEVEN_RUNNER')
                continue

            # 5. PARTIAL TP at 2.5%: sell 70%, let 30% run
            if not position.get('partial_taken') and current_price >= position['take_profit']:
                partial_qty = position['original_quantity'] * self.partial_tp_percent
                result = self.execute_sell(position, 'PARTIAL_TAKE_PROFIT', quantity=partial_qty)
                if result:
                    position['partial_taken'] = True
                    position['runner_active'] = True
                    position['stop_loss'] = position['entry_price']
                    print(f"   🏃 Runner active {symbol} - SL at entry")
                continue

            # 6. TAKE PROFIT (full, if partial not triggered)
            if current_price >= position['take_profit'] and position.get('partial_taken'):
                print(f"\n   🎯 TAKE PROFIT RUNNER {symbol} @ ${current_price:.4f}")
                self.execute_sell(position, 'TAKE_PROFIT_RUNNER')
                continue

    # ════════════════════════════════════════════════════════════════════
    # CIRCUIT BREAKER
    # ════════════════════════════════════════════════════════════════════
    def check_circuit_breaker(self):
        total = self.get_total_balance()
        if total <= self.circuit_breaker_limit:
            msg = (f"🚨 CIRCUIT BREAKER TRIGGERED\n"
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
    def _get_week_key(self):
        iso = datetime.now().date().isocalendar()
        return (iso[0], iso[1])

    def check_daily_reset(self):
        today = datetime.now().date()
        current_week = self._get_week_key()

        if current_week != self.last_week_reset_key:
            print(f"\n   🔄 New week - resetting weekly P&L")
            self.weekly_pnl = 0.0
            self.last_week_reset_key = current_week

        if today != self.last_reset_date:
            print(f"\n   🔄 New day - resetting daily counters")
            self.daily_trades = 0
            self.daily_profit = 0.0
            self.daily_loss = 0.0
            self.daily_loss_ratio = 0.0
            self.consecutive_losses = 0
            self.last_trade_time = None
            self.last_reset_date = today
            self.send_telegram(
                f"🌅 New trading day\n"
                f"Target: ${self.daily_profit_target}\n"
                f"Loss limit: ${self.max_daily_loss}"
            )

    # ════════════════════════════════════════════════════════════════════
    # CAN TRADE (single unified gate)
    # ════════════════════════════════════════════════════════════════════
    def can_trade(self):
        # Weekly loss guard
        if self.weekly_pnl <= -self.max_weekly_loss:
            return False, f"🛑 WEEKLY LOSS LIMIT: ${self.weekly_pnl:.2f}"

        # Daily profit target hit
        if self.daily_profit >= self.daily_profit_target:
            return False, f"🎯 DAILY TARGET HIT: ${self.daily_profit:.2f}"

        # Daily loss limit
        if self.daily_loss >= self.max_daily_loss:
            return False, f"🔴 DAILY LOSS LIMIT: -${self.daily_loss:.2f}"

        # Daily loss ratio
        if self.daily_loss_ratio >= 0.03:
            return False, f"🔴 DAILY LOSS RATIO: {self.daily_loss_ratio*100:.1f}%"

        # Consecutive losses
        if self.consecutive_losses >= self.max_consecutive_losses:
            return False, f"🛑 CONSECUTIVE LOSSES: {self.consecutive_losses}"

        # Hard trade cap
        if self.daily_trades >= self.hard_max_trades:
            return False, f"🛑 MAX TRADES: {self.daily_trades}/{self.hard_max_trades}"

        # Cooldown
        if self.last_trade_time:
            elapsed = (datetime.now() - self.last_trade_time).total_seconds() / 60
            if elapsed < self.trade_cooldown_minutes:
                remaining = self.trade_cooldown_minutes - elapsed
                return False, f"⏳ COOLDOWN: {remaining:.0f}min remaining"

        # Session trade limit
        session, settings = self.get_market_session()
        if self.daily_trades >= settings['max_trades']:
            return False, f"Session limit ({self.daily_trades}/{settings['max_trades']} {session.upper()})"

        return True, "OK"

    # ════════════════════════════════════════════════════════════════════
    # SYNC EXISTING POSITIONS ON STARTUP
    # ════════════════════════════════════════════════════════════════════
    def sync_existing_positions(self):
        print("\n   🔄 Syncing existing positions...")
        known_entries = {
            'BTCUSDT': 72753.0
        }
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
                print(f"   ✅ Synced: {amount:.8f} {asset} @ ${entry_price:.2f} | P&L: ${pnl:.2f}")
        except Exception as e:
            print(f"   ❌ Sync error: {e}")

    # ════════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ════════════════════════════════════════════════════════════════════
    def run(self):
        if hasattr(self, '_started'):
            return
        self._started = True

        print("\n" + "=" * 60)
        print("   🚀 SMART TRADER V2 - LIVE")
        print(f"   PID: {os.getpid()}")
        print("=" * 60)

        balance = self.get_balance()
        print(f"\n   💰 Balance: ${balance:.2f} USDT")

        last_heartbeat = datetime.now()

        while True:
            try:
                # ── Circuit breaker ───────────────────────────────────
                if not self.check_circuit_breaker():
                    break

                # ── Daily reset ───────────────────────────────────────
                self.check_daily_reset()

                # ── Heartbeat every 6 hours ───────────────────────────
                if (datetime.now() - last_heartbeat).seconds > 21600:
                    balance = self.get_balance()
                    session, _ = self.get_market_session()
                    net_pnl = self.daily_profit - self.daily_loss
                    self.send_telegram(
                        f"❤️ Heartbeat\n"
                        f"Balance: ${balance:.2f}\n"
                        f"Session: {session.upper()}\n"
                        f"Trades today: {self.daily_trades}/{self.max_trades_per_day}\n"
                        f"Daily P&L: ${net_pnl:.2f}\n"
                        f"Open: {len(self.open_positions)}"
                    )
                    last_heartbeat = datetime.now()

                # ── Manage open positions first ───────────────────────
                self.check_positions()

                # ── Position cap ──────────────────────────────────────
                if len(self.open_positions) >= self.max_positions:
                    print(f"\r   🔒 Position open - waiting for exit", end='', flush=True)
                    time.sleep(10)
                    continue

                # ── Check if trading is allowed ───────────────────────
                can_trade_result, reason = self.can_trade()

                if not can_trade_result:
                    # Hard stops - sleep until new day
                    hard_stops = ['DAILY TARGET', 'DAILY LOSS', 'WEEKLY LOSS',
                                  'MAX TRADES', 'CONSECUTIVE LOSSES']
                    if any(s in reason for s in hard_stops):
                        print(f"\n   🛑 {reason}")
                        print(f"   💤 Sleeping until next day...")
                        self.send_telegram(f"🛑 Trading stopped: {reason}")
                        while datetime.now().date() == self.last_reset_date:
                            time.sleep(300)
                        continue
                    # Soft blocks - just wait
                    print(f"\r   ⏸️ {reason}", end='', flush=True)
                    time.sleep(30)
                    continue

                # ── Scan pairs ────────────────────────────────────────
                session, settings = self.get_market_session()
                min_strength = settings['min_strength']

                print(f"\n   📊 Scanning {len(self.trading_pairs)} pairs... "
                      f"[{session.upper()} | {settings['mode']} | "
                      f"Trades: {self.daily_trades}/{settings['max_trades']}]")

                for symbol in self.trading_pairs:
                    # Skip if already in this symbol
                    if any(p['symbol'] == symbol for p in self.open_positions):
                        continue

                    # Position cap re-check
                    if len(self.open_positions) >= self.max_positions:
                        break

                    # BTC filter for alts
                    if symbol not in ('BTCUSDT',) and not self.btc_is_healthy():
                        print(f"   ⚠️ {symbol} skipped - BTC filter")
                        continue

                    signal = self.analyze(symbol)

                    # Log anything interesting
                    if signal['action'] != 'HOLD' or any(
                        x in signal.get('reason', '')
                        for x in ['HARD BLOCK', 'WAITING', 'Breakout', 'Daily target']
                    ):
                        print(f"   {symbol}: {signal['action']} "
                              f"({signal.get('market_type','N/A')}|{signal.get('zone','?')}) "
                              f"- {signal['reason']}")

                    if signal['action'] == 'BUY' and signal['strength'] >= min_strength:
                        if len(self.open_positions) >= self.max_positions:
                            break
                        self.execute_buy(symbol, signal)
                        break  # One trade per cycle

                    time.sleep(0.5)

                print(f"   ✅ Cycle complete. Next scan in 5s...")
                time.sleep(5)

            except KeyboardInterrupt:
                print("\n\n   🛑 Bot stopped by user")
                break
            except Exception as e:
                print(f"\n   ❌ Loop error: {e}")
                time.sleep(10)

        net_pnl = self.daily_profit - self.daily_loss
        print(f"\n   📊 Session Summary:")
        print(f"      Trades: {self.daily_trades}")
        print(f"      Daily P&L: ${net_pnl:.2f}")
        print(f"      Open positions: {len(self.open_positions)}")


if __name__ == '__main__':
    trader = SmartTrader()
    balance = trader.get_balance()
    trader.send_telegram(
        f"🚀 Smart Trader V2 Started\n"
        f"Balance: ${balance:.2f}\n"
        f"Target: ${trader.daily_profit_target}/day\n"
        f"Pairs: {', '.join(trader.trading_pairs)}"
    )
    trader.run()