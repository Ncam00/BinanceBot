"""
SMART TRADER V2 - Location-Based Trading Bot
=============================================
Key Features:
1. Support/Resistance detection
2. No-trade zone (skip middle 40%)
3. Market type detection (range vs trend)
4. Strategy switch per market type
5. Max 5 trades per day
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
        # 🔒 STRICT CONTROL: LIMITED COIN LIST
        # ════════════════════════════════════════════════════════════════════
        self.trading_pairs = ['ETHUSDT' , 'BTCUSDT', 'SOLUSDT', 'AVAXUSDT']
        self.max_positions = 1  # Quality over quantity
        
        # ════════════════════════════════════════════════════════════════════
        # V2 CORE SETTINGS
        # ════════════════════════════════════════════════════════════════════
        self.max_trades_per_day = 8         # Increased from 5 for more scalp opportunities
        self.daily_profit_target = 12.00     # The "Stop Trading" Goal
        self.position_size_percent = 18      # Boosted from 12% to reach $15 goal faster
        self.stop_loss_percent = 1.5         # 1.5% stop loss
        self.take_profit_percent = 2.5       # 2.5% take profit (better R:R)
        
        # ════════════════════════════════════════════════════════════════════
        # PHASE 2: ACTIVE REFLEXES
        # ════════════════════════════════════════════════════════════════════
        self.trailing_tp_distance = 0.005    # 0.5% Trail to catch the "Even More" profit
        self.break_even_profit_pct = 1.0     # Move SL to entry at 1% profit
        self.atr_multiplier = 2.0            # Standard safety buffer
        
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
            'london': {'mode': 'normal', 'max_trades': 5, 'min_strength': 0.65},
            'us': {'mode': 'aggressive', 'max_trades': 5, 'min_strength': 0.60}
        }
        
        # State tracking
        self.daily_trades = 0
        self.daily_profit = 0.0
        self.daily_loss = 0.0              # Track losses separately
        self.daily_loss_ratio = 0.0
        self.weekly_pnl = 0.0
        self.consecutive_losses = 0
        self.last_reset_date = datetime.now().date()
        self.last_week_reset_key = datetime.now().date().isocalendar()[:2]
        self.open_positions = []
        self.last_trade_time = None  # For cooldown tracking
        self.symbol_state = {}  # per-symbol state tracking
        self.trade_lock = False  # Prevents duplicate entries
        
        # ════════════════════════════════════════════════════════════════════
        # V2: HARD SAFETY RULES (CANNOT BE BYPASSED)
        # ════════════════════════════════════════════════════════════════════
        self.trade_cooldown_minutes = 30    # Wait 30min between trades
        self.hard_max_trades = 5            # ABSOLUTE max, no exceptions
        self.max_daily_loss = 10.0           # Stop if lose $10 (prevents revenge trading)
        self.max_daily_loss_ratio = 0.03     # Stop if losses hit 3% of balance
        self.max_consecutive_losses = 2      # Stop after 2 losing trades in a row
        
        # ════════════════════════════════════════════════════════════════════
        # CIRCUIT BREAKER: WEEKLY LOSS PROTECTION
        # ════════════════════════════════════════════════════════════════════
        self.STARTING_BALANCE = 468.35      # Your balance as of today
        self.MAX_LOSS_PERCENT = 0.05        # 5% hard limit
        self.STOP_TRADING_LIMIT = self.STARTING_BALANCE * (1 - self.MAX_LOSS_PERCENT)  # $444.93
        
        # ════════════════════════════════════════════════════════════════════
        # 9.5 ELITE EXECUTION SETTINGS
        # ════════════════════════════════════════════════════════════════════
        self.daily_profit_target = 12.00     # The "Stop Trading" Goal
        self.max_trades_per_day = 5          # Quality over Quantity
        self.trailing_tp_distance = 0.005    # 0.5% Trail to catch the "Even More" profit
        
        # --- THE CORE FOUR SNIPER TARGETS ---
        # These are the "Wick Zones" based on current 4H Support
        self.SNIPER_ZONES = {
            "BTCUSDT": 71250, 
            "ETHUSDT": 2185,
            "SOLUSDT": 81.25,
            "AVAXUSDT": 8.75
        }
        
        # Telegram notifications
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        # 9.5 DYNAMIC GOAL SYNC
        self.DAILY_GOAL = 15.00              # The high-end target
        self.DAILY_MIN_TARGET = 12.00        # The "Satisfaction" floor
        self.current_daily_profit = 0.0
        self.last_reset_date = datetime.now().date()
        
        print("🚀 Smart Trader V2 Initialized")
        print(f"   Max trades/day: {self.max_trades_per_day}")
        print(f"   Daily profit target: ${self.daily_profit_target}")
        print(f"   Position size: {self.position_size_percent}%")
        print(f"   Stop Loss: {self.stop_loss_percent}% | Take Profit: {self.take_profit_percent}%")
        session, settings = self.get_market_session()
        print(f"   Current session: {session.upper()} ({settings['mode']})")

        # Sync existing holdings into bot state on startup
        self.sync_existing_positions()
        
        # Update dynamic sniper zones based on current market data
        self.update_dynamic_zones()
    
    def sync_existing_positions(self):
        """Import existing holdings into bot management on startup"""
        print("\n   🔄 Checking for existing positions to sync...")
        
        known_entries = {
            'BTCUSDT': 72753.0  # Your actual average buy price
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
                    
                # Skip dust (less than $10 value)
                if amount * current_price < 10:
                    continue
                    
                # Skip if already tracked
                if any(p['symbol'] == symbol for p in self.open_positions):
                    continue
                    
                # Use known entry price or current price as fallback
                entry_price = known_entries.get(symbol, current_price)
                
                # Structure-based SL/TP
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
                    'entry_reason': 'Imported existing position on startup',
                    'market_condition': 'unknown',
                    'entry_time': datetime.now(),
                    'entry_fee': 0,
                    'entry_slippage': 0,
                    'realized_pnl': 0.0,
                    'runner_active': False,
                    'partial_taken': False,
                    'be_active': False,
                    'timestamp': datetime.now(),
                    'signal': {}
                }
                self.open_positions.append(position)
                pnl = (current_price - entry_price) * amount
                print(f"    Synced: {amount:.8f} {asset} @ entry ${entry_price:.2f}")
                print(f"      Current: ${current_price:.2f} | P&L: ${pnl:.2f}")
                print(f"      SL: ${stop_loss:.2f} | TP: ${take_profit:.2f}")
                
        except Exception as e:
            print(f"   ❌ Sync error: {e}")
    # ════════════════════════════════════════════════════════════
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
    
    def get_total_balance(self):
        """Get total balance including USDT + value of open positions"""
        try:
            usdt_balance = self.get_balance()
            position_value = 0.0
            
            for pos in self.open_positions:
                symbol = pos['symbol']
                quantity = pos['quantity']
                current_price = self.get_price(symbol)
                if current_price:
                    position_value += quantity * current_price
            
            return usdt_balance + position_value
        except Exception as e:
            print(f"   ❌ Error getting total balance: {e}")
            return self.get_balance()  # Fallback to USDT only
    
    def check_circuit_breaker(self, current_balance):
        """Kills the bot if the weekly loss limit is hit."""
        if current_balance <= self.STOP_TRADING_LIMIT:
            message = (f"🚨 CIRCUIT BREAKER TRIGGERED!\n"
                       f"Current Balance: ${current_balance:.2f}\n"
                       f"Weekly Limit: ${self.STOP_TRADING_LIMIT:.2f}\n"
                       f"Trading disabled to protect capital.")
            print(message)
            self.send_telegram(message)
            return False
        return True

    def calculate_order_fee_usdt(self, order, symbol, fallback_price=None):
        """Estimate order fees in USDT from Binance fill commissions."""
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
                    conversion_symbol = f"{commission_asset}USDT"
                    conversion_price = self.get_price(conversion_symbol)
                    if conversion_price:
                        total_fee += commission * conversion_price

            return total_fee
        except Exception:
            return 0.0

    def get_symbol_precision(self, symbol):
        """Get quantity precision from Binance LOT_SIZE filter"""
        info = self.client.get_symbol_info(symbol)
        step_size = float([f['stepSize'] for f in info['filters'] if f['filterType'] == 'LOT_SIZE'][0])
        precision = int(round(-np.log10(step_size)))
        return step_size, precision
    
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
                'reason': f"TREND BUY: Support pullback (ADX={adx['adx']:.1f}, MACD bullish)",
                'entry_type': 'PULLBACK'
            }
        
        # BUY: Breakout above resistance
        if price > resistance and trend_up and macd_bullish and ema_bullish:
            return {
                'action': 'BUY',
                'strength': 0.75,
                'reason': f"TREND BUY: Breakout above resistance",
                'entry_type': 'BREAKOUT'
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

    def btc_is_healthy(self):
        """
        Check if BTC trend is healthy before allowing alt trades.
        Returns True if safe to trade alts, False if BTC is dumping.
        """
        try:
            df = self.get_candles('BTCUSDT', '15m', 20)
            if df is None or len(df) < 10:
                return True  # If can't check, allow trading

            closes = df['close']
            current_price = closes.iloc[-1]
            price_15m_ago = closes.iloc[-2]
            price_1h_ago = closes.iloc[-4]

            # Calculate short term moves
            change_15m = ((current_price - price_15m_ago) / price_15m_ago) * 100
            change_1h = ((current_price - price_1h_ago) / price_1h_ago) * 100

            # BTC dumping hard - block alt buys
            if change_15m < -0.5 or change_1h < -1.5:
                print(f"   ⚠️ BTC FILTER: BTC dropping ({change_1h:.2f}% 1h) - blocking alts")
                return False

            return True

        except Exception as e:
            print(f"   ⚠️ BTC filter check failed: {e}")
            return True  # On error, allow trading

    def get_symbol_state(self, symbol):
        """Get or initialize per-symbol breakout state."""
        if symbol not in self.symbol_state:
            self.symbol_state[symbol] = {
                'waiting_for_retest': False,
                'breakout_level': None,
                'breakout_direction': None,
                'retest_candles': 0,
            }
        return self.symbol_state[symbol]

    def reset_breakout_state(self, symbol):
        """Clear breakout retest state for a specific symbol"""
        self.symbol_state[symbol] = {
            'waiting_for_retest': False,
            'breakout_level': None,
            'breakout_direction': None,
            'retest_candles': 0
        }

    def update_risk_controls(self, profit, balance):
        """Update daily loss ratio and consecutive losses, then return risk status."""
        safe_balance = max(balance, 1e-9)

        if profit < 0:
            self.daily_loss_ratio += abs(profit) / safe_balance
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        if self.daily_loss_ratio >= self.max_daily_loss_ratio:
            return 'STOP_DAILY_LOSS'

        if self.consecutive_losses >= self.max_consecutive_losses:
            return 'STOP_CONSECUTIVE_LOSSES'

        return 'OK'

    def reset_daily(self):
        """Reset risk counters for a new trading day."""
        self.daily_loss = 0.0
        self.daily_loss_ratio = 0.0
        self.consecutive_losses = 0

    def reset_weekly(self):
        """Reset weekly realized PnL when a new ISO week starts."""
        self.weekly_pnl = 0.0

    def get_week_key(self):
        """Return (year, week) for weekly guard resets."""
        today = datetime.now().date()
        iso = today.isocalendar()
        return (iso.year, iso.week)

    def get_recent_high(self, df, lookback=20):
        """Recent breakout level from highs before the active retest candles."""
        if len(df) <= 3:
            return df['high'].max()
        start_index = max(0, len(df) - lookback - 3)
        end_index = len(df) - 3
        return df['high'].iloc[start_index:end_index].max()

    def is_breakout(self, df, buffer=0.001):
        """Require a close above the recent high to avoid weak breakouts."""
        recent_high = self.get_recent_high(df)
        recent_closes = df['close'].iloc[-3:]
        return recent_closes.max() > recent_high * (1 + buffer)

    def is_retest(self, df, tolerance=0.002):
        """Current price should retest the broken level instead of chasing higher."""
        recent_high = self.get_recent_high(df)
        current_price = df['close'].iloc[-1]
        return abs(current_price - recent_high) / recent_high < tolerance

    def bullish_confirmation(self, df):
        """Breakout retest confirmation: current candle is bullish and clears prior high."""
        if len(df) < 2:
            return False
        last_candle = df.iloc[-1]
        previous_candle = df.iloc[-2]
        return last_candle['close'] > last_candle['open'] and last_candle['close'] > previous_candle['high']

    def breakout_retest_entry(self, df):
        """Second entry condition: breakout, retest, and bullish follow-through."""
        return self.is_breakout(df) and self.is_retest(df) and self.bullish_confirmation(df)
    
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

    def valid_breakout_setup(self, df, price, rsi, macd, signal, prev_macd, ema):
        """Breakout entry validation: retest entry with momentum and trend alignment."""
        if rsi <= 50 or rsi >= 70:
            return False

        if not (macd > signal and macd > prev_macd):
            return False

        if price < ema:
            return False

        return True

    def log_trade(self, trade_data):
        """Write a structured trade record as JSON lines."""
        log_path = os.path.join(os.path.dirname(__file__), 'trade_log.jsonl')
        with open(log_path, 'a', encoding='utf-8') as log_file:
            log_file.write(json.dumps(trade_data, ensure_ascii=True) + "\n")
    
    # ════════════════════════════════════════════════════════════════════
    # V2: MAIN ANALYSIS (LOCATION-BASED)
    # ════════════════════════════════════════════════════════════════════
    def analyze(self, symbol):
        """
        V2 Analysis: Location-based trading
        Only generates signals when price is at key levels
        """
        # STEP 2: The "Soft Ceiling" check
        # This stops NEW trades if you've hit the daily profit target,
        # but keeps the bot alive for active trades to keep trailing.
        if self.total_daily_profit >= self.daily_profit_target:
            return {'action': 'HOLD', 'strength': 0, 'reason': 'Daily profit target reached - no new trade scans'}

        # --- Your existing scanning logic below ---
        print(f"🔍 Scanning {symbol}...")
        df = self.get_candles(symbol, '15m', 100)
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
        current_open = df['open'].iloc[-1]
        current_close = df['close'].iloc[-1]
        state = self.get_symbol_state(symbol)

        # Stateful breakout tracking: detect breakout first, then wait for retest.
        tolerance = 0.002

        if market_type == 'TREND' and price > resistance and not state['waiting_for_retest']:
            state['waiting_for_retest'] = True
            state['breakout_level'] = resistance
            state['breakout_direction'] = 'LONG'
            state['retest_candles'] = 0
            self.send_telegram(
                f"📈 {symbol} Breakout detected\nLevel: {state['breakout_level']:.4f}\nWaiting for retest..."
            )
            return {
                'action': 'HOLD',
                'strength': 0,
                'reason': f"⏳ WAITING: Breakout detected at ${resistance:.4f}, waiting for retest",
                'entry_type': 'BREAKOUT',
                'market_type': market_type,
                'price': price,
                'support': support,
                'support_override': resistance,
                'resistance': resistance,
                'rsi': rsi,
                'adx': adx['adx'],
                'zone': 'breakout_wait'
            }

        if state.get('waiting_for_retest', False):
            state['retest_candles'] += 1
            if state['retest_candles'] > 1:
                pass  # Only alert on first retest candle

            if state['retest_candles'] > 10:
                self.reset_breakout_state(symbol)
                return {
                    'action': 'HOLD',
                    'strength': 0,
                    'reason': '⏳ WAITING: Breakout retest expired after 10 candles',
                    'market_type': market_type,
                    'price': price,
                    'support': support,
                    'resistance': resistance,
                    'rsi': rsi,
                    'adx': adx['adx'],
                    'zone': 'breakout_timeout'
                }

            if state['breakout_direction'] == 'LONG' and price <= state['breakout_level'] * (1 + tolerance):
                if state['retest_candles'] == 1:
                    self.send_telegram(
                        f"🔁 {symbol} Retest happening at {price:.4f}"
                    )
                if current_close > current_open and rsi > 50:
                    signal = {
                        'action': 'BUY',
                        'strength': 0.80,
                        'reason': f"BREAKOUT BUY: Retest confirmed at ${state['breakout_level']:.4f}",
                        'entry_type': 'BREAKOUT',
                        'support_override': state['breakout_level'],
                        'clear_breakout_wait': True
                    }
                else:
                    return {
                        'action': 'HOLD',
                        'strength': 0,
                        'reason': '⏳ WAITING: Retest touched but confirmation candle not ready',
                        'entry_type': 'BREAKOUT',
                        'market_type': market_type,
                        'price': price,
                        'support': support,
                        'support_override': state['breakout_level'],
                        'resistance': resistance,
                        'rsi': rsi,
                        'adx': adx['adx'],
                        'zone': 'breakout_retest'
                    }
            else:
                return {
                    'action': 'HOLD',
                    'strength': 0,
                    'reason': f"⏳ WAITING: Watching breakout retest at ${state['breakout_level']:.4f}",
                    'entry_type': 'BREAKOUT',
                    'market_type': market_type,
                    'price': price,
                    'support': support,
                    'support_override': state['breakout_level'],
                    'resistance': resistance,
                    'rsi': rsi,
                    'adx': adx['adx'],
                    'zone': 'breakout_wait'
                }
        
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
        if signal['action'] == 'BUY' and signal.get('entry_type') != 'BREAKOUT':
            if not self.has_confirmation_candle(df, 'bullish'):
                signal = {
                    'action': 'HOLD',
                    'strength': 0,
                    'reason': f"⏳ WAITING: Buy signal but no confirmation candle yet"
                }
        
        # ════════════════════════════════════════════════════════════════════
        # ETH/BTC setup validation
        # ════════════════════════════════════════════════════════════════════
        if signal['action'] == 'BUY':
            prices_list = closes.tolist()
            entry_type = signal.get('entry_type', 'PULLBACK')
            if entry_type == 'BREAKOUT':
                if not self.valid_breakout_setup(df, price, rsi, macd['macd'], macd['signal'], macd['prev_macd'], ema_slow):
                    signal = {
                        'action': 'HOLD',
                        'strength': 0,
                        'reason': f"⏳ WAITING: Breakout retest conditions not met"
                    }
            else:
                if not self.valid_eth_setup(price, prices_list, rsi, macd['macd'], macd['signal'], macd['prev_macd'], ema_slow):
                    signal = {
                        'action': 'HOLD',
                        'strength': 0,
                        'reason': f"⏳ WAITING: Not all setup conditions met"
                    }

        # Clear breakout state if trade confirmed
        if signal.get('clear_breakout_wait'):
            self.reset_breakout_state(symbol)
        
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
        if self.trade_lock:
            print(f"   🔒 TRADE LOCK ACTIVE - skipping duplicate entry for {symbol}")
            return None
        self.trade_lock = True
        try:
            balance = self.get_balance()
            price = signal['price']
            entry_time = datetime.now()
            
            # Get support for stop loss calculation
            support = signal.get('support_override', signal.get('support', price * 0.985))
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
            step_size, precision = self.get_symbol_precision(symbol)
            quantity = round(quantity, precision)
            
            # Execute order
            order = self.client.create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            
            fill_price = float(order['fills'][0]['price'])
            entry_fee = self.calculate_order_fee_usdt(order, symbol, fallback_price=fill_price)
            
            # Use pre-calculated stop loss (structure-based)
            stop_loss = stop_loss_price
            
            # First take profit at 1R, then manage the runner at breakeven.
            actual_risk = fill_price - stop_loss
            take_profit = fill_price + (actual_risk * 1.0)
            rr_target = round((take_profit - fill_price) / max(actual_risk, 1e-9), 2)
            
            # ════════════════════════════════════════════════════════════════════
            # 🔒 IMMEDIATELY update state (CRITICAL)
            # ════════════════════════════════════════════════════════════════════
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
                'runner_active': False,
                'partial_taken': False,
                'be_active': False,
                'timestamp': datetime.now(),
                'signal': signal
            }
            self.open_positions.append(position)     # open_position = True
            self.last_trade_time = datetime.now()    # Start cooldown timer
            self.daily_trades += 1                   # trades_today += 1
            entry_type = signal.get('entry_type', 'PULLBACK')
            if signal.get('clear_breakout_wait'):
                self.reset_breakout_state(symbol)
            
            msg = f"🚀 TRADE OPENED\n"
            msg += f"Pair: {symbol}\n"
            msg += f"Type: {entry_type}\n"
            msg += f"Entry: ${fill_price:.4f}\n"
            msg += f"SL: ${position['stop_loss']:.4f}\n"
            msg += f"TP: ${position['take_profit']:.4f}"
            
            print(f"\n   {msg.replace(chr(10), chr(10) + '   ')}")
            self.send_telegram(msg)
            
            self.trade_lock = False
            return position
            
        except Exception as e:
            print(f"   ❌ Buy failed: {e}")
            self.trade_lock = False
            return None
        finally:
            self.trade_lock = False
    
    def calculate_dynamic_sniper(self, symbol):
        """Calculate dynamic entry point based on 24h market lows"""
        try:
            # 1. Fetch the last 24 hours of 1H candles
            bars = self.get_candles(symbol, '1h', 24)
            if bars is None or len(bars) < 24:
                return None
            
            lows = bars['low'].values
            current_price = bars['close'].iloc[-1]
            
            # 2. Find the 'Floor' (The lowest wick in 24 hours)
            market_floor = min(lows)
            
            # 3. Set the 'Trap' (1.2% below the floor to catch the shakeout)
            dynamic_entry = market_floor * 0.988
            
            # 4. Safety Guard: If market is pumping, move entry to 2% below current price
            if dynamic_entry < (current_price * 0.95):
                dynamic_entry = current_price * 0.98
            
            return round(dynamic_entry, 8)  # Round to avoid precision issues
        except Exception as e:
            print(f"   ❌ Error calculating dynamic sniper for {symbol}: {e}")
            return None
    
    def update_dynamic_zones(self):
        """Automatically updates Sniper Zones to current 4H Support"""
        for symbol in self.trading_pairs:
            try:
                df = self.get_candles(symbol, '4h', 20)
                if df is not None:
                    # Set sniper entry at the lowest wick of the last 20 4H candles
                    self.SNIPER_ZONES[symbol] = df['low'].min()
                    print(f"   🎯 Dynamic Sniper Set: {symbol} @ {self.SNIPER_ZONES[symbol]}")
            except Exception as e:
                print(f"   ⚠️ Sniper Update Failed for {symbol}: {e}")
    
    def place_sniper_order(self, symbol, target_price, quantity):
        """Place a limit buy order at a specific target price"""
        try:
            # Get symbol precision
            step_size, precision = self.get_symbol_precision(symbol)
            quantity = round(quantity, precision)
            
            # Places a LIMIT order instead of a MARKET order
            order = self.client.create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,  # Good 'Til Canceled
                quantity=quantity,
                price=str(target_price)  # Binance expects string for price
            )
            self.send_telegram(f"🎯 Sniper Trap Set: {symbol} Limit Buy at ${target_price}")
            print(f"   🎯 Sniper order placed: {symbol} @ ${target_price}")
            return order
        except Exception as e:
            print(f"   ❌ Error placing sniper order: {e}")
            return None
    
    def place_trailing_take_profit(self, symbol, quantity, side='SELL'):
        """
        The "Secret Sauce" for the $18 goal.
        Follows the price up and only sells if it drops 0.5% from the peak.
        """
        try:
            step_size, precision = self.get_symbol_precision(symbol)
            quantity = round(quantity, precision)

            # 50 BIPS = 0.5% (Your trailing_tp_distance)
            delta_bips = int(self.trailing_tp_distance * 10000)

            # Use STOP_LOSS (Market) for Spot Trailing to ensure execution
            order = self.client.create_order(
                symbol=symbol,
                side=side,
                type='STOP_LOSS',
                quantity=quantity,
                trailingDelta=delta_bips
            )
            print(f"   ✅ Trailing Profit Lock active for {symbol} at {delta_bips} BIPS")
            self.send_telegram(f"🔒 Trailing TP set for {symbol} at {delta_bips} BIPS")
            return order
        except Exception as e:
            print(f"   ❌ Trailing Order failed: {e}")
            # Fallback to a standard market sell if trailing fails
            return self.client.order_market_sell(symbol=symbol, quantity=quantity)

    def execute_95_dynamic_entry(self, symbol, side, quantity):
        """
        9.5 ELITE EXECUTION: Enters the trade and immediately
        sets a Trailing Stop to capture the $12-$18 range.
        """
        try:
            step_size, precision = self.get_symbol_precision(symbol)
            quantity = round(quantity, precision)

            # 1. Market Buy Entry
            order = self.client.create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )

            # 2. Calculate the Trailing Delta (0.5% = 50 BIPS)
            delta_bips = int(self.trailing_tp_distance * 10000)

            # 3. Immediate Trailing Stop Activation
            self.client.create_order(
                symbol=symbol,
                side=SIDE_SELL if side == SIDE_BUY else SIDE_BUY,
                type='STOP_LOSS',
                quantity=quantity,
                trailingDelta=delta_bips
            )

            self.send_telegram(f"🎯 <b>9.5 Sniper Active:</b> {symbol}\n"
                               f"Trailing Profit Lock: {self.trailing_tp_distance*100}%")
            return order
        except Exception as e:
            print(f"   ❌ Execution Error: {e}")
            return None

    def execute_sell(self, position, reason='SIGNAL', quantity=None):
        """Execute a sell order"""
        try:
            symbol = position['symbol']
            sell_quantity = position['quantity'] if quantity is None else quantity
            exit_time = datetime.now()
            step_size, precision = self.get_symbol_precision(symbol)
            sell_quantity = round(sell_quantity, precision)
            
            if sell_quantity <= 0:
                print(f"   ⚠️ Sell quantity too small for {symbol}, skipping")
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
            
            # Update daily profit/loss tracking
            if pnl >= 0:
                self.daily_profit += pnl
            else:
                self.daily_loss += abs(pnl)  # Track losses as positive number
            self.weekly_pnl += pnl
            
            # ════════════════════════════════════════════════════════════════════
            # 🔓 Position closed - open_position = False
            # ════════════════════════════════════════════════════════════════════
            remaining_quantity = round(position['quantity'] - sell_quantity, precision)
            if remaining_quantity <= 0:
                self.open_positions = [p for p in self.open_positions if p['symbol'] != symbol]
            else:
                position['quantity'] = remaining_quantity

            original_quantity = max(position.get('original_quantity', sell_quantity), 1e-9)
            entry_fee_share = position.get('entry_fee', 0.0) * (sell_quantity / original_quantity)
            fees = round(entry_fee_share + exit_fee, 4)
            risk_per_unit = max(position['entry_price'] - position['stop_loss'], 1e-9)
            rr_achieved = round((fill_price - position['entry_price']) / risk_per_unit, 2)
            duration = round((exit_time - position.get('entry_time', exit_time)).total_seconds() / 60, 2)
            strategy = 'breakout_retest' if position.get('entry_type') == 'breakout' else 'pullback'
            notes = 'clean retest + strong momentum' if strategy == 'breakout_retest' else position.get('entry_reason', '')

            self.log_trade({
                'id': position.get('trade_id'),
                'pair': symbol,
                'strategy': strategy,
                'entry_price': position['entry_price'],
                'exit_price': fill_price,
                'position_size': sell_quantity,
                'risk_percent': position.get('risk_percent'),
                'stop_loss': position['stop_loss'],
                'take_profit': position['take_profit'],
                'rr_target': position.get('rr_target'),
                'rr_achieved': rr_achieved,
                'profit': round(pnl, 4),
                'win': pnl > 0,
                'fees': fees,
                'entry_time': position.get('entry_time').isoformat() if position.get('entry_time') else None,
                'exit_time': exit_time.isoformat(),
                'duration_minutes': duration,
                'market_condition': position.get('market_condition'),
                'entry_reason': position.get('entry_reason'),
                'exit_reason': reason,
                'slippage': round(position.get('entry_slippage', 0.0), 6),
                'notes': notes
            })
            
            # Net P&L for display
            balance = self.get_balance()

            if remaining_quantity <= 0:
                risk_status = self.update_risk_controls(total_trade_pnl, balance)
                if risk_status != 'OK':
                    print(f"   🛑 Trading stopped: {risk_status}")
            
            if remaining_quantity > 0:
                msg = f"✅ PARTIAL TAKE PROFIT\n"
            else:
                msg = f"✅ TRADE CLOSED\n"
            msg += f"Pair: {symbol}\n"
            msg += f"PnL: ${pnl:.2f} ({pnl_percent:+.2f}%)\n"
            msg += f"New Balance: ${balance:.2f}"
            
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
        """Reset daily counters at midnight and weekly counters on new week"""
        today = datetime.now().date()
        current_week_key = self.get_week_key()

        if current_week_key != self.last_week_reset_key:
            print(f"\n   🔄 New week - resetting weekly P&L guard")
            self.reset_weekly()
            self.last_week_reset_key = current_week_key

        if today != self.last_reset_date:
            print(f"\n   🔄 New day - resetting counters")
            self.daily_trades = 0
            self.daily_profit = 0.0
            self.reset_daily()
            self.last_trade_time = None  # Reset cooldown too
            self.last_reset_date = today

    @property
    def total_daily_profit(self):
        return self.daily_profit

    def can_trade(self):
        """The 9.5 Permission Gate: Checks goals and limits before any trade."""
        today = datetime.now().date()

        # 1. Midnight Reset
        if today > self.last_reset_date:
            print(f"🌅 New Day: {today}. Resetting profit/loss trackers.")
            self.current_daily_profit = 0.0
            self.daily_trades = 0
            self.last_reset_date = today

        # 2. Check Daily Profit Goal ($12)
        if self.current_daily_profit >= self.DAILY_GOAL:
            return False, "Goal Met"  # Returns TWO things

        # 3. Check Hard Loss Limit ($10)
        if self.daily_loss >= self.max_daily_loss:
            return False, "Daily Loss Limit Hit"

        # 4. Check Trade Count (Max 5)
        if self.daily_trades >= self.max_trades_per_day:
            return False, "Max Trades Hit"

        return True, "Ready"  # Returns TWO things
    

    # ════════════════════════════════════════════════════════════════════
    # POSITION MANAGEMENT
    # ════════════════════════════════════════════════════════════════════
    def check_positions(self):
        """Check open positions for SL/TP and trailing stop"""
        for position in self.open_positions[:]:
            symbol = position['symbol']
            current_price = self.get_price(symbol)
            if not current_price:
                continue

            # Calculate current P&L
            pnl_percent = ((current_price - position['entry_price']) / position['entry_price']) * 100

            # ════════════════════════════════════════════════════════════
            # TRAILING STOP LOGIC
            # Activates after 1.5% profit, trails at 0.8% distance
            # ════════════════════════════════════════════════════════════
            if pnl_percent >= 1.5:
                # Initialize trailing stop if not set
                if not position.get('trailing_stop_active'):
                    position['trailing_stop_active'] = True
                    position['highest_price'] = current_price
                    position['trailing_stop_price'] = current_price * (1 - 0.008)
                    print(f"   🔒 TRAILING STOP ACTIVATED {symbol} @ ${position['trailing_stop_price']:.4f}")
                    self.send_telegram(
                        f"🔒 Trailing Stop Activated\n"
                        f"Pair: {symbol}\n"
                        f"Profit: +{pnl_percent:.2f}%\n"
                        f"Trail: ${position['trailing_stop_price']:.4f}"
                    )

                # Update trailing stop if price moves higher
                if current_price > position.get('highest_price', 0):
                    position['highest_price'] = current_price
                    new_trail = current_price * (1 - 0.008)
                    if new_trail > position['trailing_stop_price']:
                        position['trailing_stop_price'] = new_trail
                        print(f"   📈 TRAILING STOP RAISED {symbol} @ ${position['trailing_stop_price']:.4f}")

                # Check if trailing stop hit
                if current_price <= position['trailing_stop_price']:
                    print(f"\n   🔒 TRAILING STOP HIT {symbol} @ ${current_price:.4f}")
                    self.execute_sell(position, 'TRAILING_STOP')
                    continue

            # Runner logic: after partial TP, exit remainder at breakeven
            if position.get('runner_active') and current_price <= position['entry_price']:
                print(f"\n   ⚖️ BREAKEVEN EXIT {symbol}")
                self.execute_sell(position, 'BREAKEVEN_RUNNER')
                continue

            # Partial close at first target: take 70% off, move stop to entry
            if not position.get('partial_taken') and current_price >= position['take_profit']:
                partial_quantity = position['original_quantity'] * 0.70
                result = self.execute_sell(position, 'PARTIAL_TAKE_PROFIT', quantity=partial_quantity)
                if result:
                    position['partial_taken'] = True
                    position['runner_active'] = True
                    position['stop_loss'] = position['entry_price']
                    print(f"   🏃 Runner active for {symbol} - stop moved to breakeven")
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
    # PHASE 2: ACTIVE TRADE MANAGEMENT
    # ════════════════════════════════════════════════════════════════════
    def manage_active_trades(self):
        """Phase 2: Active defense and profit trailing (9.5 Protocol)"""
        for pos in self.open_positions[:]:
            symbol = pos['symbol']
            current_price = self.get_price(symbol)
            if not current_price:
                continue

            entry = pos['entry_price']
            profit_pct = (current_price - entry) / entry * 100

            # 1. THE BREAK-EVEN SHIELD (at 1%)
            if profit_pct >= 1.0 and not pos.get('be_active', False):
                pos['stop_loss'] = entry
                pos['be_active'] = True
                print(f"   🛡️ {symbol} is Risk-Free! SL moved to Break-Even @ ${entry:.4f}")

            # 2. Track the high water mark
            if 'highest_price' not in pos:
                pos['highest_price'] = entry
            if current_price > pos['highest_price']:
                pos['highest_price'] = current_price

            # 3. 9.5 TRAILING TAKE PROFIT (Trigger: 2.5% | Callback: 0.5%)
            should_sell, new_high = self.apply_trailing_stop(
                current_price,
                entry,
                pos['highest_price']
            )
            pos['highest_price'] = new_high

            if should_sell:
                print(f"   🎯 TTP Triggered for {symbol}! Closing for profit.")
                self.execute_sell(pos, 'TRAILING_TP')
                continue

            # 4. THE REVERSAL EXIT (price hit stop loss)
            if current_price <= pos['stop_loss']:
                print(f"\n   🚀 Trade closing {symbol}: price ${current_price:.4f} hit SL ${pos['stop_loss']:.4f}")
                self.execute_sell(pos, 'ACTIVE_DEFENSE')

    def manage_open_trades(self):
        """9.5 Protocol: Manage open positions with break-even, trailing TP, and hard stop."""
        for pos in self.open_positions[:]:
            symbol = pos['symbol']
            current_price = self.get_price(symbol)
            if not current_price:
                continue

            # Calculate current profit %
            profit_pct = (current_price - pos['entry_price']) / pos['entry_price'] * 100

            # 1. BREAK-EVEN SHIELD
            # Move SL to entry at 1.0% profit (per your settings)
            if profit_pct >= self.break_even_profit_pct and not pos.get('be_active', False):
                pos['stop_loss'] = pos['entry_price']
                pos['be_active'] = True
                print(f"   🛡️ {symbol} is now RISK-FREE (SL moved to Entry)")

            # 2. UPDATE HIGH WATER MARK (For Trailing)
            if current_price > pos.get('highest_price', 0):
                pos['highest_price'] = current_price

            # 3. TRAILING TAKE-PROFIT (The 2% Weekly Growth Engine)
            if profit_pct >= self.take_profit_percent:
                # If price drops 0.5% from its peak, SELL
                callback_price = pos['highest_price'] * (1 - self.trailing_tp_distance)
                if current_price <= callback_price:
                    print(f"   🎯 TTP TRIGGERED: Closing {symbol} at {profit_pct:.2f}%")
                    self.execute_sell(pos, 'TRAILING_TP')
                    continue

            # 4. HARD STOP LOSS
            elif current_price <= pos['stop_loss']:
                print(f"   🚨 STOP LOSS: Closing {symbol} at ${current_price:.4f}")
                self.execute_sell(pos, 'STOP_LOSS')

    def manage_exit_logic(self, position, current_price):
        """9.5 Exit Strategy: Break-even shield, partial sell, and trailing runner."""
        entry = position['entry_price']
        profit_pct = (current_price - entry) / entry * 100

        # STEP 1: At 1% profit, move Stop Loss to Break Even (Safe Trade)
        if profit_pct >= 1.0 and not position.get('be_active', False):
            position['stop_loss'] = entry
            position['be_active'] = True

        # STEP 2: At 2.5% profit, sell HALF (Locks in the first $5-$7)
        if profit_pct >= 2.5 and not position.get('partial_taken', False):
            partial_qty = position['original_quantity'] * 0.5
            self.execute_sell(position, 'PARTIAL_TAKE_PROFIT', quantity=partial_qty)
            position['partial_taken'] = True
            # STEP 3: Let the rest trail for the $15 goal
            position['stop_loss'] = entry * 1.015

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
                # Check circuit breaker first
                current_wallet = self.get_total_balance()
                if not self.check_circuit_breaker(current_wallet):
                    break  # This stops the bot completely
                
                # 1. Manage current money first (active defense)
                self.manage_active_trades()
                
                # 2. Check daily resets
                self.check_daily_reset()
                
                # 3. Look for new opportunities if we have room
                if self.daily_trades < self.max_trades_per_day:
                    can_trade_result, reason = self.can_trade()
                    
                    if can_trade_result:
                        for symbol in self.trading_pairs:
                            # Skip if we already have position in this symbol
                            if any(p['symbol'] == symbol for p in self.open_positions):
                                continue
                            
                            # Re-check: One position max
                            if len(self.open_positions) >= self.max_positions:
                                break
                            
                            # BTC correlation filter for alts
                            if symbol != 'BTCUSDT' and not self.btc_is_healthy():
                                continue
                            
                            # Analyze for valid trade setup
                            signal = self.analyze(symbol)
                            
                            # Check if valid trade setup
                            session, settings = self.get_market_session()
                            min_strength = settings['min_strength']
                            
                            if signal['action'] == 'BUY' and signal['strength'] >= min_strength:
                                # Final position check
                                if len(self.open_positions) >= self.max_positions:
                                    break
                                
                                # Place trade
                                self.execute_buy(symbol, signal)
                                break  # ONE TRADE ONLY
                
                time.sleep(30)  # Check every 30 seconds
                
            except KeyboardInterrupt:
                print("\n\n   🛑 Stopping bot...")
                break
            except Exception as e:
                print(f"\n   ❌ Error: {e}")
                time.sleep(30)
        
        # Final summary
        print(f"\n   📊 Session Summary:")
        print(f"      Trades today: {self.daily_trades}")
        print(f"      Daily P&L: ${self.daily_profit:.2f}")
        print(f"      Open positions: {len(self.open_positions)}")
    
    def run_95_logic(self):
        """9.5 Elite Sniper Logic"""
        current_balance = self.get_total_balance()
        
        # Periodically update dynamic zones (every 6 hours)
        if not hasattr(self, 'last_zone_update') or \
           (datetime.now() - self.last_zone_update).total_seconds() > 21600:
            self.update_dynamic_zones()
            self.last_zone_update = datetime.now()
        
        # 1. SHIELD: The Anti-Zero Check
        if current_balance <= (self.INITIAL_DEPOSIT * (1 - self.WEEKLY_SAFETY_NET)):
            self.kill_all_trades("5% Weekly Loss Limit Hit. Protecting capital.")
            return

        # 2. PROFIT GUARD: Take the win and walk away
        if self.total_daily_profit >= self.DAILY_PROFIT_GOAL:
            print("   🎯 Daily Goal Achieved. See you tomorrow!")
            return

        # 3. SPEAR: Place the Sniper Traps at dynamic zones
        for coin, price in self.SNIPER_ZONES.items():
            if not self.has_open_order(coin):
                # Calculate position size
                quantity = self.calculate_position_size(current_balance, price, price * 0.97, 0.02)  # 2% risk
                if quantity > 0:
                    # We place LIMIT orders to catch the wicks, not market orders
                    self.place_sniper_order(coin, price, quantity)
                    self.send_telegram(f"🎯 9.5 Sniper Trap Set for {coin} at ${price}")

        # 4. TRAILING SHIELD: Lock in the profit
        self.monitor_trailing_stops(threshold=0.01, lock_in=self.TRAILING_STOP)
    
    def has_open_order(self, symbol):
        """Check if we have an open order for this symbol"""
        try:
            orders = self.client.get_open_orders(symbol=symbol)
            return len(orders) > 0
        except:
            return False
    
    def is_trade_allowed_by_location(self, symbol):
        """Check if symbol is in a tradeable zone (not in the 40% chop middle)."""
        price = self.get_price(symbol)
        df = self.get_candles(symbol, '1h', 50)
        if not price or df is None or len(df) < 10:
            return False
        sr = self.calculate_support_resistance(df)
        zone = self.get_trade_zone(price, sr['support'], sr['resistance'])
        if zone == 'middle':
            print(f"   🛑 {symbol} in No-Trade Zone (40% Chop). Skipping.")
            return False
        return True

    def check_breakout_velocity(self, symbol, interval='4h', limit=20, threshold=2.0):
        """Check if current price movement exceeds historical volatility (z-score)."""
        df = self.get_candles(symbol, interval, limit)
        if df is None or len(df) < 5:
            return None, 0.0

        prices = df['close'].values
        mean = np.mean(prices)
        std_dev = np.std(prices)
        if std_dev == 0:
            return None, 0.0

        current_price = prices[-1]
        z_score = (current_price - mean) / std_dev

        if z_score > threshold:
            print(f"   🚀 {symbol} High-Velocity Breakout (+{z_score:.2f} SD)")
            return 'BREAKOUT', z_score
        elif z_score < -threshold:
            print(f"   📉 {symbol} High-Velocity Breakdown ({z_score:.2f} SD)")
            return 'BREAKDOWN', z_score
        return 'CONSOLIDATION', z_score

    def kill_all_trades(self, reason):
        """Close all open positions"""
        print(f"   🚨 KILLING ALL TRADES: {reason}")
        for pos in self.open_positions[:]:
            self.execute_sell(pos, reason)
        self.send_telegram(f"🚨 All trades closed: {reason}")
    
    def monitor_trailing_stops(self, threshold=0.01, lock_in=0.005):
        """Monitor and adjust trailing stops for open positions"""
        for pos in self.open_positions:
            current_price = self.get_price(pos['symbol'])
            if not current_price:
                continue
            
            entry = pos['entry_price']
            profit_pct = (current_price - entry) / entry * 100
            
            # Only trail if above threshold
            if profit_pct >= threshold * 100:
                new_trail = current_price * (1 - lock_in)
                if new_trail > pos['stop_loss']:
                    pos['stop_loss'] = new_trail
                    print(f"   📈 Trailing stop updated for {pos['symbol']}: ${new_trail:.4f}")

    def apply_trailing_stop(self, current_price, entry_price, highest_price):
        """
        9.5 Protocol: Trailing Take-Profit Logic
        Trigger: 2.5% | Callback: 0.5%
        """
        profit_pct = (current_price - entry_price) / entry_price * 100

        # 1. Activation: Has it hit our minimum 2.5%?
        if profit_pct >= 2.5:
            # 2. Update the "High Water Mark"
            if current_price > highest_price:
                highest_price = current_price
                return False, highest_price  # Keep holding, price is rising

            # 3. Callback Check: Has it dropped 0.5% from the peak?
            drop_from_peak = (highest_price - current_price) / highest_price * 100
            if drop_from_peak >= 0.5:
                return True, highest_price  # SELL NOW - Trend is reversing

        return False, highest_price  # Still below trigger, keep holding


if __name__ == '__main__':
    trader = SmartTrader()
    # Telegram ONLY here (not inside run())
    balance = trader.get_balance()
    trader.send_telegram(f"🚀 Smart Trader V2 Started\nBalance: ${balance:.2f}\nMax trades: {trader.max_trades_per_day}/day\nTarget: ${trader.daily_profit_target}/day")
    trader.run()
