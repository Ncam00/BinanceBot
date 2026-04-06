"""
SMART TRADER V2 - Location-Based Trading Bot
=============================================
Key Features:
1. Support/Resistance detection
2. No-trade zone (skip middle 40%)
3. Market type detection (range vs trend)
4. Strategy switch per market type
5. Max 2 trades per day
6. Profit lock at $5 daily
7. Location-based entries only

Target: $5+/day with ~60-65% win rate
"""

import os
import time
import json
from datetime import datetime, timedelta
from binance.client import Client
from binance.enums import *
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import requests
import pytz

# Load environment variables
load_dotenv()

class SmartTrader:
    def __init__(self):
        # Binance API
        self.client = Client(
            os.getenv('BINANCE_API_KEY'),
            os.getenv('BINANCE_SECRET_KEY')
        )
        
        # ════════════════════════════════════════════════════════════════════
        # 🔒 STRICT CONTROL: ONE COIN ONLY
        # ════════════════════════════════════════════════════════════════════
        self.trading_pairs = ['ETHUSDT']  # ONE COIN ONLY - safest/most liquid
        self.max_positions = 1            # ONE POSITION AT A TIME
        
        # ════════════════════════════════════════════════════════════════════
        # V2 CORE SETTINGS
        # ════════════════════════════════════════════════════════════════════
        self.max_trades_per_day = 2          # Only 2 trades max
        self.daily_profit_target = 5.0       # Stop at $5 profit
        self.position_size_percent = 12      # 12% per trade
        self.stop_loss_percent = 1.5         # 1.5% stop loss
        self.take_profit_percent = 2.5       # 2.5% take profit (better R:R)
        
        # Location-based trading settings
        self.sr_lookback = 50                # Candles for S/R detection
        self.no_trade_zone_percent = 40      # Skip middle 40% of range
        self.near_level_percent = 1.5        # Within 1.5% of S/R level
        
        # ADX thresholds for market type
        self.adx_range_threshold = 20        # ADX < 20 = ranging
        self.adx_trend_threshold = 25        # ADX > 25 = trending
        
        # ════════════════════════════════════════════════════════════════════
        # V2: SESSION-BASED TRADING (NZ TIME)
        # Asia (11:00-19:00): Low risk, max 1 trade
        # London (19:00-03:00): Normal trading
        # US (03:00-11:00): Aggressive, best volatility
        # ════════════════════════════════════════════════════════════════════
        self.nz_timezone = pytz.timezone('Pacific/Auckland')
        self.session_settings = {
            'asia': {'mode': 'low_risk', 'max_trades': 1, 'min_strength': 0.85},
            'london': {'mode': 'normal', 'max_trades': 2, 'min_strength': 0.75},
            'us': {'mode': 'aggressive', 'max_trades': 2, 'min_strength': 0.70}
        }
        
        # State tracking
        self.daily_trades = 0
        self.daily_profit = 0.0
        self.daily_loss = 0.0              # Track losses separately
        self.last_reset_date = datetime.now().date()
        self.open_positions = []
        self.last_trade_time = None  # For cooldown tracking
        
        # ════════════════════════════════════════════════════════════════════
        # V2: HARD SAFETY RULES (CANNOT BE BYPASSED)
        # ════════════════════════════════════════════════════════════════════
        self.trade_cooldown_minutes = 30    # Wait 30min between trades
        self.hard_max_trades = 2             # ABSOLUTE max, no exceptions
        self.max_daily_loss = 10.0           # Stop if lose $10 (prevents revenge trading)
        
        # Telegram notifications
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        print("🚀 Smart Trader V2 Initialized")
        print(f"   Max trades/day: {self.max_trades_per_day}")
        print(f"   Daily profit target: ${self.daily_profit_target}")
        print(f"   Position size: {self.position_size_percent}%")
        print(f"   Stop Loss: {self.stop_loss_percent}% | Take Profit: {self.take_profit_percent}%")
        session, settings = self.get_market_session()
        print(f"   Current session: {session.upper()} ({settings['mode']})")
    
    # ════════════════════════════════════════════════════════════════════
    # V2: SESSION DETECTION (NZ TIMEZONE)
    # ════════════════════════════════════════════════════════════════════
    def get_nz_hour(self):
        """Get current hour in NZ timezone"""
        nz_now = datetime.now(self.nz_timezone)
        return nz_now.hour
    
    def get_market_session(self):
        """
        Determine market session based on NZ time
        Returns: (session_name, session_settings)
        """
        hour = self.get_nz_hour()
        
        # Asia session: 11:00 - 19:00 NZT (low volatility)
        if 11 <= hour < 19:
            return 'asia', self.session_settings['asia']
        
        # London session: 19:00 - 03:00 NZT (medium volatility)
        elif hour >= 19 or hour < 3:
            return 'london', self.session_settings['london']
        
        # US session: 03:00 - 11:00 NZT (high volatility - best for trading)
        else:
            return 'us', self.session_settings['us']
    
    # ════════════════════════════════════════════════════════════════════
    # TELEGRAM NOTIFICATIONS
    # ════════════════════════════════════════════════════════════════════
    def send_telegram(self, message):
        """Send Telegram notification"""
        if not self.telegram_token or not self.telegram_chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            requests.post(url, data={
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }, timeout=5)
        except:
            pass
    
    # ════════════════════════════════════════════════════════════════════
    # DATA FETCHING
    # ════════════════════════════════════════════════════════════════════
    def get_candles(self, symbol, interval='1m', limit=100):
        """Fetch OHLCV candle data"""
        try:
            klines = self.client.get_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            return df
        except Exception as e:
            print(f"   ❌ Error fetching candles for {symbol}: {e}")
            return None
    
    def get_price(self, symbol):
        """Get current price"""
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except:
            return None
    
    def get_balance(self):
        """Get USDT balance"""
        try:
            account = self.client.get_account()
            for asset in account['balances']:
                if asset['asset'] == 'USDT':
                    return float(asset['free'])
            return 0.0
        except Exception as e:
            print(f"   ❌ Error getting balance: {e}")
            return 0.0
    
    # ════════════════════════════════════════════════════════════════════
    # TECHNICAL INDICATORS
    # ════════════════════════════════════════════════════════════════════
    def calculate_rsi(self, closes, period=14):
        """Calculate RSI"""
        delta = closes.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
    
    def calculate_macd(self, closes):
        """Calculate MACD"""
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
        """Calculate EMA"""
        return closes.ewm(span=period, adjust=False).mean().iloc[-1]
    
    def calculate_adx(self, df, period=14):
        """Calculate ADX for trend strength"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        plus_dm = high.diff()
        minus_dm = low.diff().abs() * -1
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm > 0] = 0
        minus_dm = minus_dm.abs()
        
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
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
        """Calculate Bollinger Bands"""
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
            'pb': pb  # %B - position within bands (0-1)
        }
    
    # ════════════════════════════════════════════════════════════════════
    # V2: SUPPORT/RESISTANCE DETECTION
    # ════════════════════════════════════════════════════════════════════
    def calculate_support_resistance(self, df):
        """Find support and resistance levels from swing points"""
        highs = df['high'].values
        lows = df['low'].values
        
        # Find swing highs (local maxima)
        swing_highs = []
        for i in range(2, len(highs) - 2):
            if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and
                highs[i] > highs[i+1] and highs[i] > highs[i+2]):
                swing_highs.append(highs[i])
        
        # Find swing lows (local minima)
        swing_lows = []
        for i in range(2, len(lows) - 2):
            if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and
                lows[i] < lows[i+1] and lows[i] < lows[i+2]):
                swing_lows.append(lows[i])
        
        # Use recent high/low as fallback
        period_high = max(highs[-self.sr_lookback:])
        period_low = min(lows[-self.sr_lookback:])
        
        # Primary resistance = highest recent swing high
        resistance = max(swing_highs[-3:]) if len(swing_highs) >= 3 else period_high
        
        # Primary support = lowest recent swing low
        support = min(swing_lows[-3:]) if len(swing_lows) >= 3 else period_low
        
        return {
            'support': support,
            'resistance': resistance,
            'range': resistance - support,
            'mid_point': (resistance + support) / 2
        }
    
    # ════════════════════════════════════════════════════════════════════
    # V2: NO-TRADE ZONE CHECK
    # ════════════════════════════════════════════════════════════════════
    def is_in_no_trade_zone(self, price, support, resistance):
        """Check if price is in the middle 'chop' zone"""
        range_size = resistance - support
        if range_size <= 0:
            return False
        
        zone_percent = self.no_trade_zone_percent / 100
        lower_bound = support + (range_size * ((1 - zone_percent) / 2))
        upper_bound = resistance - (range_size * ((1 - zone_percent) / 2))
        
        return lower_bound < price < upper_bound
    
    def is_near_level(self, price, level):
        """Check if price is near a key level"""
        distance = abs(price - level) / level * 100
        return distance <= self.near_level_percent
    
    # ════════════════════════════════════════════════════════════════════
    # V2: TRADE ZONE DETECTION (HARD BLOCK)
    # ════════════════════════════════════════════════════════════════════
    def get_trade_zone(self, price, support, resistance):
        """
        Determine which zone price is in
        Returns: 'buy_zone', 'sell_zone', or 'middle' (NO TRADE)
        """
        range_size = resistance - support
        if range_size <= 0:
            return 'middle'  # Invalid range = no trade
        
        # Buy zone: bottom 30% of range
        buy_zone_top = support + (range_size * 0.30)
        
        # Sell zone: top 30% of range  
        sell_zone_bottom = resistance - (range_size * 0.30)
        
        if price <= buy_zone_top:
            return 'buy_zone'
        elif price >= sell_zone_bottom:
            return 'sell_zone'
        else:
            return 'middle'  # MIDDLE 40% = NO TRADE
    
    # ════════════════════════════════════════════════════════════════════
    # V2: CONFIRMATION CANDLE CHECK
    # ════════════════════════════════════════════════════════════════════
    def has_confirmation_candle(self, df, direction='bullish'):
        """
        Check if last closed candle confirms the direction
        - Bullish: Green candle (close > open)
        - Bearish: Red candle (close < open)
        """
        if len(df) < 2:
            return False
        
        # Use second-to-last candle (last one may not be closed)
        candle = df.iloc[-2]
        
        if direction == 'bullish':
            # Green candle with decent body
            body = candle['close'] - candle['open']
            candle_range = candle['high'] - candle['low']
            if candle_range == 0:
                return False
            body_ratio = body / candle_range
            return body > 0 and body_ratio > 0.3  # At least 30% body
        else:
            # Red candle
            body = candle['open'] - candle['close']
            candle_range = candle['high'] - candle['low']
            if candle_range == 0:
                return False
            body_ratio = body / candle_range
            return body > 0 and body_ratio > 0.3
    
    # ════════════════════════════════════════════════════════════════════
    # V2: MARKET TYPE DETECTION
    # ════════════════════════════════════════════════════════════════════
    def get_market_type(self, adx_value):
        """Determine if market is ranging or trending"""
        if adx_value < self.adx_range_threshold:
            return 'RANGE'
        elif adx_value >= self.adx_trend_threshold:
            return 'TREND'
        else:
            return 'MIXED'
    
    # ════════════════════════════════════════════════════════════════════
    # V2: STRATEGY-SPECIFIC SIGNALS
    # ════════════════════════════════════════════════════════════════════
    def get_range_signal(self, price, rsi, bb, support, resistance):
        """
        RANGE STRATEGY: Mean-reversion
        - Buy at support when RSI low
        - Sell at resistance when RSI high
        """
        near_support = self.is_near_level(price, support)
        near_resistance = self.is_near_level(price, resistance)
        
        # BUY: Near support + RSI oversold/low
        if near_support and rsi < 40 and bb['pb'] < 0.2:
            return {
                'action': 'BUY',
                'strength': 0.8,
                'reason': f"RANGE BUY: Near support (RSI={rsi:.1f}, BB%={bb['pb']:.2f})"
            }
        
        # SELL: Near resistance + RSI overbought/high
        if near_resistance and rsi > 60 and bb['pb'] > 0.8:
            return {
                'action': 'SELL',
                'strength': 0.8,
                'reason': f"RANGE SELL: Near resistance (RSI={rsi:.1f}, BB%={bb['pb']:.2f})"
            }
        
        return {'action': 'HOLD', 'strength': 0, 'reason': 'Range: Not at key level'}
    
    def get_trend_signal(self, price, rsi, macd, ema_fast, ema_slow, adx, support, resistance):
        """
        TREND STRATEGY: Breakout/momentum
        - Buy on bullish breakout with MACD + EMA confirmation
        - Sell on bearish breakdown
        """
        near_support = self.is_near_level(price, support)
        near_resistance = self.is_near_level(price, resistance)
        
        macd_bullish = macd['macd'] > macd['signal'] and macd['histogram'] > macd['prev_histogram']
        macd_bearish = macd['macd'] < macd['signal'] and macd['histogram'] < macd['prev_histogram']
        ema_bullish = ema_fast > ema_slow
        ema_bearish = ema_fast < ema_slow
        trend_up = adx['plus_di'] > adx['minus_di']
        
        # BUY: Pullback to support in uptrend
        if near_support and trend_up and macd_bullish and ema_bullish and rsi < 50:
            return {
                'action': 'BUY',
                'strength': 0.85,
                'reason': f"TREND BUY: Support pullback (ADX={adx['adx']:.1f}, MACD bullish)"
            }
        
        # BUY: Breakout above resistance
        if price > resistance and trend_up and macd_bullish and ema_bullish:
            return {
                'action': 'BUY',
                'strength': 0.75,
                'reason': f"TREND BUY: Breakout above resistance"
            }
        
        # SELL: Breakdown below support
        if price < support and not trend_up and macd_bearish:
            return {
                'action': 'SELL',
                'strength': 0.85,
                'reason': f"TREND SELL: Breakdown below support"
            }
        
        return {'action': 'HOLD', 'strength': 0, 'reason': 'Trend: No clear setup'}
    
    # ════════════════════════════════════════════════════════════════════
    # ETH SETUP VALIDATION (IMPROVED)
    # ════════════════════════════════════════════════════════════════════
    def get_support(self, prices, lookback=20):
        """Step 1: Dynamic support from recent lows"""
        return min(prices[-lookback:])
    
    def rsi_ok(self, rsi):
        """Step 2: RSI condition - not overbought"""
        return rsi < 55
    
    def momentum_ok(self, macd, signal, prev_macd):
        """Step 3: Momentum confirmation - MACD rising AND above signal"""
        return macd > signal and macd > prev_macd
    
    def trend_ok(self, price, ema):
        """Step 4: Only trade WITH the trend"""
        return price > ema
    
    def valid_eth_setup(self, price, prices, rsi, macd, signal, prev_macd, ema):
        """Final validation: ALL conditions must pass"""
        # Dynamic support from last 20 candles
        support = self.get_support(prices, lookback=20)
        
        # Check if price is near support (within 0.3%)
        if abs(price - support) / price > 0.003:
            return False
        
        # RSI not overbought
        if rsi >= 55:
            return False
        
        # MACD rising and above signal
        if not (macd > signal and macd > prev_macd):
            return False
        
        # Price above EMA (with trend)
        if price < ema:
            return False
        
        return True
    
    # ════════════════════════════════════════════════════════════════════
    # V2: MAIN ANALYSIS (LOCATION-BASED)
    # ════════════════════════════════════════════════════════════════════
    def analyze(self, symbol):
        """
        V2 Analysis: Location-based trading
        Only generates signals when price is at key levels
        """
        df = self.get_candles(symbol, '1m', 100)
        if df is None or len(df) < 50:
            return {'action': 'HOLD', 'strength': 0, 'reason': 'Insufficient data'}
        
        closes = df['close']
        price = closes.iloc[-1]
        
        # Calculate indicators
        rsi = self.calculate_rsi(closes)
        macd = self.calculate_macd(closes)
        ema_fast = self.calculate_ema(closes, 7)
        ema_slow = self.calculate_ema(closes, 18)
        adx = self.calculate_adx(df)
        bb = self.calculate_bollinger(closes)
        
        # V2: Support/Resistance
        sr = self.calculate_support_resistance(df)
        support = sr['support']
        resistance = sr['resistance']
        
        # V2: Market type
        market_type = self.get_market_type(adx['adx'])
        
        # ════════════════════════════════════════════════════════════════════
        # 🚫🚫🚫 ABSOLUTE HARD BLOCK - NO EXCEPTIONS 🚫🚫🚫
        # If NOT near support AND NOT near resistance → IMMEDIATE RETURN
        # This is NOT scoring. This is NOT soft. This STOPS EVERYTHING.
        # ════════════════════════════════════════════════════════════════════
        near_support = self.is_near_level(price, support)
        near_resistance = self.is_near_level(price, resistance)
        
        if not near_support and not near_resistance:
            return {
                'action': 'HOLD',
                'strength': 0,
                'reason': f"🚫 HARD BLOCK: Not at support/resistance (IMMEDIATE RETURN)",
                'market_type': market_type,
                'price': price,
                'support': support,
                'resistance': resistance,
                'rsi': rsi,
                'adx': adx['adx'],
                'zone': 'middle'
            }
        
        # Also check zone (belt and suspenders)
        zone = self.get_trade_zone(price, support, resistance)
        
        if zone == 'middle':
            return {
                'action': 'HOLD',
                'strength': 0,
                'reason': f"🚫 HARD BLOCK: Price in middle zone (NO TRADE)",
                'market_type': market_type,
                'price': price,
                'support': support,
                'resistance': resistance,
                'rsi': rsi,
                'adx': adx['adx'],
                'zone': zone
            }
        
        # V2: Strategy switch based on market type
        if market_type == 'RANGE':
            signal = self.get_range_signal(price, rsi, bb, support, resistance)
        elif market_type == 'TREND':
            signal = self.get_trend_signal(price, rsi, macd, ema_fast, ema_slow, adx, support, resistance)
        else:
            # MIXED market - be extra cautious
            signal = {'action': 'HOLD', 'strength': 0, 'reason': 'MIXED market - waiting for clarity'}
        
        # ════════════════════════════════════════════════════════════════════
        # CONFIRMATION CANDLE CHECK (For BUY signals only)
        # Don't just buy on signal - wait for confirmation
        # ════════════════════════════════════════════════════════════════════
        if signal['action'] == 'BUY':
            if not self.has_confirmation_candle(df, 'bullish'):
                signal = {
                    'action': 'HOLD',
                    'strength': 0,
                    'reason': f"⏳ WAITING: Buy signal but no confirmation candle yet"
                }
        
        # ════════════════════════════════════════════════════════════════════
        # ETH SETUP VALIDATION (Final check)
        # Price near support + RSI ok + Momentum ok + Trend ok
        # ════════════════════════════════════════════════════════════════════
        if signal['action'] == 'BUY':
            prices_list = closes.tolist()
            if not self.valid_eth_setup(price, prices_list, rsi, macd['macd'], macd['signal'], macd['prev_macd'], ema_slow):
                signal = {
                    'action': 'HOLD',
                    'strength': 0,
                    'reason': f"⏳ WAITING: Not all ETH setup conditions met (support/RSI/momentum/trend)"
                }
        
        # Add metadata
        signal['market_type'] = market_type
        signal['price'] = price
        signal['support'] = support
        signal['resistance'] = resistance
        signal['rsi'] = rsi
        signal['adx'] = adx['adx']
        signal['zone'] = zone
        
        return signal
    
    # ════════════════════════════════════════════════════════════════════
    # POSITION SIZING (Risk-Based)
    # ════════════════════════════════════════════════════════════════════
    def calculate_position_size(self, account_balance, entry_price, stop_loss_price, risk_percent=0.015):
        """
        Calculate position size based on risk percent and stop loss distance.
        """
        # 1. How much you're willing to lose
        risk_amount = account_balance * risk_percent
        
        # 2. Distance between entry and stop
        risk_per_unit = abs(entry_price - stop_loss_price)
        
        # Safety check (avoid division by zero)
        if risk_per_unit == 0:
            return 0
        
        # 3. Position size
        position_size = risk_amount / risk_per_unit
        
        # 4. Optional cap (prevents overexposure)
        max_position_value = account_balance * 0.25  # max 25% of account
        max_position_size = max_position_value / entry_price
        
        position_size = min(position_size, max_position_size)
        
        # Safety: Minimum trade size (Binance requirement)
        if position_size * entry_price < 10:
            return 0  # skip trade if too small
        
        # Safety: Avoid absurdly large size
        if position_size <= 0:
            return 0
        
        return position_size
    
    # ════════════════════════════════════════════════════════════════════
    # ORDER EXECUTION
    # ════════════════════════════════════════════════════════════════════
    def execute_buy(self, symbol, signal):
        """Execute a buy order"""
        try:
            balance = self.get_balance()
            price = signal['price']
            
            # Get support for stop loss calculation
            support = signal.get('support', price * 0.985)
            structure_sl = support * 0.995  # 0.5% below support (safer buffer)
            max_sl = price * 0.97  # Never risk more than 3%
            stop_loss_price = max(structure_sl, max_sl)
            
            # Adjust risk based on session
            session, _ = self.get_market_session()
            if session == "asia":
                risk_percent = 0.01  # safer (1%)
            else:
                risk_percent = 0.015  # normal (1.5%)
            
            # Calculate position size using risk-based method
            quantity = self.calculate_position_size(balance, price, stop_loss_price, risk_percent)
            
            if quantity == 0:
                print(f"   ⚠️ Position size too small, skipping trade")
                return None
            
            # Get symbol info for precision
            info = self.client.get_symbol_info(symbol)
            step_size = float([f['stepSize'] for f in info['filters'] if f['filterType'] == 'LOT_SIZE'][0])
            precision = int(round(-np.log10(step_size)))
            quantity = round(quantity, precision)
            
            # Execute order
            order = self.client.create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            
            fill_price = float(order['fills'][0]['price'])
            
            # Use pre-calculated stop loss (structure-based)
            stop_loss = stop_loss_price
            
            # Take profit based on R:R from actual risk (2:1 minimum)
            actual_risk = fill_price - stop_loss
            take_profit = fill_price + (actual_risk * 2.0)
            
            # ════════════════════════════════════════════════════════════════════
            # 🔒 IMMEDIATELY update state (CRITICAL)
            # ════════════════════════════════════════════════════════════════════
            position = {
                'symbol': symbol,
                'quantity': quantity,
                'entry_price': fill_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'timestamp': datetime.now(),
                'signal': signal
            }
            self.open_positions.append(position)     # open_position = True
            self.last_trade_time = datetime.now()    # Start cooldown timer
            self.daily_trades += 1                   # trades_today += 1
            
            msg = f"🟢 BUY {symbol}\n"
            msg += f"Qty: {quantity} @ ${fill_price:.4f}\n"
            msg += f"Reason: {signal['reason']}\n"
            msg += f"SL: ${position['stop_loss']:.4f} | TP: ${position['take_profit']:.4f}"
            
            print(f"\n   {msg.replace(chr(10), chr(10) + '   ')}")
            self.send_telegram(msg)
            
            return position
            
        except Exception as e:
            print(f"   ❌ Buy failed: {e}")
            return None
    
    def execute_sell(self, position, reason='SIGNAL'):
        """Execute a sell order"""
        try:
            symbol = position['symbol']
            quantity = position['quantity']
            
            order = self.client.create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            
            fill_price = float(order['fills'][0]['price'])
            pnl = (fill_price - position['entry_price']) * quantity
            pnl_percent = ((fill_price / position['entry_price']) - 1) * 100
            
            # Update daily profit/loss tracking
            if pnl >= 0:
                self.daily_profit += pnl
            else:
                self.daily_loss += abs(pnl)  # Track losses as positive number
            
            # ════════════════════════════════════════════════════════════════════
            # 🔓 Position closed - open_position = False
            # ════════════════════════════════════════════════════════════════════
            self.open_positions = [p for p in self.open_positions if p['symbol'] != symbol]
            
            # Net P&L for display
            net_pnl = self.daily_profit - self.daily_loss
            
            emoji = "🟢" if pnl > 0 else "🔴"
            msg = f"{emoji} SELL {symbol} ({reason})\n"
            msg += f"Qty: {quantity} @ ${fill_price:.4f}\n"
            msg += f"P&L: ${pnl:.2f} ({pnl_percent:+.2f}%)\n"
            msg += f"Daily: +${self.daily_profit:.2f} / -${self.daily_loss:.2f} (Net: ${net_pnl:.2f})"
            
            print(f"\n   {msg.replace(chr(10), chr(10) + '   ')}")
            self.send_telegram(msg)
            
            return {'pnl': pnl, 'pnl_percent': pnl_percent}
            
        except Exception as e:
            print(f"   ❌ Sell failed: {e}")
            return None
    
    # ════════════════════════════════════════════════════════════════════
    # V2: DAILY LIMITS
    # ════════════════════════════════════════════════════════════════════
    def check_daily_reset(self):
        """Reset daily counters at midnight"""
        today = datetime.now().date()
        if today != self.last_reset_date:
            print(f"\n   🔄 New day - resetting counters")
            self.daily_trades = 0
            self.daily_profit = 0.0
            self.daily_loss = 0.0
            self.last_trade_time = None  # Reset cooldown too
            self.last_reset_date = today
    
    def can_trade(self):
        """Check if we can make more trades today - HARD BLOCKS"""
        # ════════════════════════════════════════════════════════════════════
        # HARD BLOCK 1: Absolute trade limit (CANNOT BE BYPASSED)
        # ════════════════════════════════════════════════════════════════════
        if self.daily_trades >= self.hard_max_trades:
            return False, f"🛑 HARD LIMIT: {self.daily_trades}/{self.hard_max_trades} trades (BLOCKED)"
        
        # ════════════════════════════════════════════════════════════════════
        # HARD BLOCK 2: Max daily LOSS protection (prevents revenge trading)
        # ════════════════════════════════════════════════════════════════════
        if self.daily_loss >= self.max_daily_loss:
            return False, f"🔴 MAX LOSS HIT: -${self.daily_loss:.2f} (STOP - NO REVENGE TRADING)"
        
        # ════════════════════════════════════════════════════════════════════
        # HARD BLOCK 3: Cooldown between trades (30 min)
        # ════════════════════════════════════════════════════════════════════
        if self.last_trade_time:
            time_since_trade = (datetime.now() - self.last_trade_time).total_seconds() / 60
            if time_since_trade < self.trade_cooldown_minutes:
                remaining = self.trade_cooldown_minutes - time_since_trade
                return False, f"⏳ COOLDOWN: {remaining:.0f}min remaining"
        
        # ════════════════════════════════════════════════════════════════════
        # HARD BLOCK 4: Daily profit target reached (session-aware)
        # ════════════════════════════════════════════════════════════════════
        session, settings = self.get_market_session()
        
        # Session-aware profit lock: Lower target during low-volatility Asia
        session_target = 3.0 if session == 'asia' else self.daily_profit_target
        
        if self.daily_profit >= session_target:
            return False, f"🎯 PROFIT LOCKED: ${self.daily_profit:.2f} >= ${session_target} ({session.upper()} target hit)"
        
        # Session-specific trade limit
        session_max = settings['max_trades']
        if self.daily_trades >= session_max:
            return False, f"Session limit ({self.daily_trades}/{session_max} for {session.upper()})"
        
        return True, "OK"
    
    # ════════════════════════════════════════════════════════════════════
    # POSITION MANAGEMENT
    # ════════════════════════════════════════════════════════════════════
    def check_positions(self):
        """Check open positions for SL/TP"""
        for position in self.open_positions[:]:  # Copy list for safe modification
            symbol = position['symbol']
            current_price = self.get_price(symbol)
            if not current_price:
                continue
            
            # Check stop loss
            if current_price <= position['stop_loss']:
                print(f"\n   🛑 STOP LOSS HIT {symbol}")
                self.execute_sell(position, 'STOP_LOSS')
                continue
            
            # Check take profit
            if current_price >= position['take_profit']:
                print(f"\n   🎯 TAKE PROFIT HIT {symbol}")
                self.execute_sell(position, 'TAKE_PROFIT')
                continue
    
    # ════════════════════════════════════════════════════════════════════
    # MAIN TRADING LOOP
    # ════════════════════════════════════════════════════════════════════
    def run(self):
        """Main trading loop"""
        # Startup lock - prevent multiple runs
        if hasattr(self, 'started'):
            return
        self.started = True
        
        print("\n" + "="*60)
        print("   🚀 SMART TRADER V2 - STARTING")
        print(f"   Bot PID: {os.getpid()}")
        print("="*60)
        
        balance = self.get_balance()
        print(f"\n   💰 Balance: ${balance:.2f} USDT")
        
        while True:
            try:
                # Loop heartbeat (for debugging restarts)
                print(f"\r   ⏱️ Loop running... {int(time.time())}", end='', flush=True)
                
                # Reset daily counters if new day
                self.check_daily_reset()
                
                # Check open positions for SL/TP
                self.check_positions()
                
                # ════════════════════════════════════════════════════════════════════
                # 🔒 HARD GUARDS (FIRST) - Must pass ALL before any trading
                # ════════════════════════════════════════════════════════════════════
                
                # GUARD 1: Open position check
                if len(self.open_positions) >= self.max_positions:
                    print(f"\r   🔒 Position open - waiting for exit (no new trades)", end='', flush=True)
                    time.sleep(10)
                    continue
                
                # GUARD 2: Daily trade limit
                if self.daily_trades >= self.hard_max_trades:
                    print(f"\n   🛑 GLOBAL LIMIT: {self.daily_trades} trades today - STOPPING")
                    time.sleep(300)
                    continue
                
                # GUARD 3: Cooldown check (recently traded)
                if self.last_trade_time:
                    time_since_trade = (datetime.now() - self.last_trade_time).total_seconds() / 60
                    if time_since_trade < self.trade_cooldown_minutes:
                        remaining = self.trade_cooldown_minutes - time_since_trade
                        print(f"\r   ⏳ COOLDOWN: {remaining:.0f}min remaining", end='', flush=True)
                        time.sleep(30)
                        continue
                
                # GUARD 4: Profit/Loss limits (can_trade handles full logic)
                can_trade_result, reason = self.can_trade()
                
                if not can_trade_result:
                    # HARD STOP - These BREAK the loop entirely
                    if "PROFIT LOCKED" in reason or "MAX LOSS" in reason:
                        print(f"\n\n   🛑 {reason}")
                        print(f"   💤 TRADING STOPPED FOR TODAY - Bot will sleep until midnight")
                        self.send_telegram(f"🛑 Trading stopped: {reason}")
                        
                        # Sleep until next day (true stop)
                        while datetime.now().date() == self.last_reset_date:
                            time.sleep(300)  # Check every 5 minutes
                        continue  # New day, reset and continue
                    
                    # Other blocks - just wait
                    print(f"\r   ⏸️ Trading paused: {reason}", end='', flush=True)
                    time.sleep(30)
                    continue
                
                # Get current session
                session, settings = self.get_market_session()
                min_strength = settings['min_strength']
                session_max = settings['max_trades']
                
                # ════════════════════════════════════════════════════════════════════
                # 🔍 THEN check strategy - Scan for valid setups
                # ════════════════════════════════════════════════════════════════════
                print(f"\n   📊 Scanning {len(self.trading_pairs)} pairs... [Session: {session.upper()} | Mode: {settings['mode']} | Trades: {self.daily_trades}/{session_max}]")
                
                for symbol in self.trading_pairs:
                    # Skip if we already have position in this symbol
                    if any(p['symbol'] == symbol for p in self.open_positions):
                        continue
                    
                    # 🔒 Re-check: One position max
                    if len(self.open_positions) >= self.max_positions:
                        break
                    
                    # Analyze for valid trade setup
                    signal = self.analyze(symbol)
                    
                    # Log interesting signals
                    if signal['action'] != 'HOLD' or any(x in signal.get('reason', '') for x in ['HARD BLOCK', 'WAITING', 'NO-TRADE']):
                        market_type = signal.get('market_type', 'N/A')
                        zone = signal.get('zone', '?')
                        print(f"   {symbol}: {signal['action']} ({market_type}|{zone}) - {signal['reason']}")
                    
                    # Check if valid trade setup
                    if signal['action'] == 'BUY' and signal['strength'] >= min_strength:
                        # Final position check
                        if len(self.open_positions) >= self.max_positions:
                            print(f"   🔒 Already have position - BLOCKED")
                            break
                        
                        # ════════════════════════════════════════════════════════════
                        # 💰 Place trade
                        # State update (open_position, last_trade_time, trades_today)
                        # happens IMMEDIATELY inside execute_buy()
                        # ════════════════════════════════════════════════════════════
                        self.execute_buy(symbol, signal)
                        break  # ONE TRADE ONLY
                    
                    time.sleep(0.5)
                
                print(f"   ✅ Cycle complete. Waiting 10s...")
                time.sleep(10)
                
            except KeyboardInterrupt:
                print("\n\n   🛑 Stopping bot...")
                break
            except Exception as e:
                print(f"\n   ❌ Error: {e}")
                time.sleep(10)
        
        # Final summary
        print(f"\n   📊 Session Summary:")
        print(f"      Trades today: {self.daily_trades}")
        print(f"      Daily P&L: ${self.daily_profit:.2f}")
        print(f"      Open positions: {len(self.open_positions)}")


if __name__ == '__main__':
    trader = SmartTrader()
    # Telegram ONLY here (not inside run())
    balance = trader.get_balance()
    trader.send_telegram(f"🚀 Smart Trader V2 Started\nBalance: ${balance:.2f}\nMax trades: {trader.max_trades_per_day}/day\nTarget: ${trader.daily_profit_target}/day")
    trader.run()
