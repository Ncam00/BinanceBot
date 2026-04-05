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

# Load environment variables
load_dotenv()

class SmartTrader:
    def __init__(self):
        # Binance API
        self.client = Client(
            os.getenv('BINANCE_API_KEY'),
            os.getenv('BINANCE_SECRET_KEY')
        )
        
        # Trading settings
        self.trading_pairs = [
            'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
            'AVAXUSDT', 'LINKUSDT', 'ADAUSDT', 'DOTUSDT', 'FETUSDT', 'NEARUSDT'
        ]
        
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
        
        # State tracking
        self.daily_trades = 0
        self.daily_profit = 0.0
        self.last_reset_date = datetime.now().date()
        self.open_positions = []
        
        # Telegram notifications
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        print("🚀 Smart Trader V2 Initialized")
        print(f"   Max trades/day: {self.max_trades_per_day}")
        print(f"   Daily profit target: ${self.daily_profit_target}")
        print(f"   Position size: {self.position_size_percent}%")
        print(f"   Stop Loss: {self.stop_loss_percent}% | Take Profit: {self.take_profit_percent}%")
    
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
            'prev_histogram': histogram.iloc[-2] if len(histogram) > 1 else 0
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
        
        # V2: NO-TRADE ZONE CHECK
        if self.is_in_no_trade_zone(price, support, resistance):
            return {
                'action': 'HOLD',
                'strength': 0,
                'reason': f"⚠️ NO-TRADE ZONE: Price in middle ({self.no_trade_zone_percent}% zone)",
                'market_type': market_type,
                'price': price,
                'support': support,
                'resistance': resistance,
                'rsi': rsi,
                'adx': adx['adx']
            }
        
        # V2: Strategy switch based on market type
        if market_type == 'RANGE':
            signal = self.get_range_signal(price, rsi, bb, support, resistance)
        elif market_type == 'TREND':
            signal = self.get_trend_signal(price, rsi, macd, ema_fast, ema_slow, adx, support, resistance)
        else:
            # MIXED market - be extra cautious
            signal = {'action': 'HOLD', 'strength': 0, 'reason': 'MIXED market - waiting for clarity'}
        
        # Add metadata
        signal['market_type'] = market_type
        signal['price'] = price
        signal['support'] = support
        signal['resistance'] = resistance
        signal['rsi'] = rsi
        signal['adx'] = adx['adx']
        
        return signal
    
    # ════════════════════════════════════════════════════════════════════
    # ORDER EXECUTION
    # ════════════════════════════════════════════════════════════════════
    def execute_buy(self, symbol, signal):
        """Execute a buy order"""
        try:
            balance = self.get_balance()
            price = signal['price']
            
            # Calculate position size
            position_value = balance * (self.position_size_percent / 100)
            quantity = position_value / price
            
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
            
            # ════════════════════════════════════════════════════════════════════
            # V2: STRUCTURE-BASED STOP LOSS
            # Use support level instead of fixed %, avoids noise stops
            # ════════════════════════════════════════════════════════════════════
            support = signal.get('support', fill_price * 0.985)
            
            # Stop loss = just below support (with small buffer)
            structure_sl = support * 0.998  # 0.2% below support
            
            # Fallback: use percentage if structure SL is too far (>3%)
            percent_sl = fill_price * (1 - self.stop_loss_percent / 100)
            max_sl = fill_price * 0.97  # Never risk more than 3%
            
            # Use the tighter of: structure SL or max allowed
            stop_loss = max(structure_sl, max_sl)
            
            # Take profit based on R:R from actual risk
            actual_risk = fill_price - stop_loss
            take_profit = fill_price + (actual_risk * 2.0)  # 2:1 R:R minimum
            
            # Track position
            position = {
                'symbol': symbol,
                'quantity': quantity,
                'entry_price': fill_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'timestamp': datetime.now(),
                'signal': signal
            }
            self.open_positions.append(position)
            self.daily_trades += 1
            
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
            
            # Update daily profit
            self.daily_profit += pnl
            
            # Remove from open positions
            self.open_positions = [p for p in self.open_positions if p['symbol'] != symbol]
            
            emoji = "🟢" if pnl > 0 else "🔴"
            msg = f"{emoji} SELL {symbol} ({reason})\n"
            msg += f"Qty: {quantity} @ ${fill_price:.4f}\n"
            msg += f"P&L: ${pnl:.2f} ({pnl_percent:+.2f}%)\n"
            msg += f"Daily Total: ${self.daily_profit:.2f}"
            
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
            self.last_reset_date = today
    
    def can_trade(self):
        """Check if we can make more trades today"""
        # Check max trades
        if self.daily_trades >= self.max_trades_per_day:
            return False, f"Max trades reached ({self.daily_trades}/{self.max_trades_per_day})"
        
        # Check profit target
        if self.daily_profit >= self.daily_profit_target:
            return False, f"Profit target reached (${self.daily_profit:.2f} >= ${self.daily_profit_target})"
        
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
        print("\n" + "="*60)
        print("   🚀 SMART TRADER V2 - STARTING")
        print("="*60)
        
        balance = self.get_balance()
        print(f"\n   💰 Balance: ${balance:.2f} USDT")
        
        self.send_telegram(f"🚀 Smart Trader V2 Started\nBalance: ${balance:.2f}\nMax trades: {self.max_trades_per_day}/day\nTarget: ${self.daily_profit_target}/day")
        
        while True:
            try:
                # Reset daily counters if new day
                self.check_daily_reset()
                
                # Check open positions first
                self.check_positions()
                
                # Check if we can trade
                can_trade, reason = self.can_trade()
                
                if not can_trade:
                    print(f"\r   ⏸️ Trading paused: {reason}", end='', flush=True)
                    time.sleep(30)
                    continue
                
                # Analyze all pairs
                print(f"\n   📊 Scanning {len(self.trading_pairs)} pairs... (Trades: {self.daily_trades}/{self.max_trades_per_day}, Profit: ${self.daily_profit:.2f})")
                
                for symbol in self.trading_pairs:
                    # Skip if we already have position in this symbol
                    if any(p['symbol'] == symbol for p in self.open_positions):
                        continue
                    
                    # Analyze
                    signal = self.analyze(symbol)
                    
                    # Log interesting signals
                    if signal['action'] != 'HOLD' or 'NO-TRADE ZONE' in signal.get('reason', ''):
                        market_type = signal.get('market_type', 'N/A')
                        print(f"   {symbol}: {signal['action']} ({market_type}) - {signal['reason']}")
                    
                    # Execute buy if signal is strong enough
                    if signal['action'] == 'BUY' and signal['strength'] >= 0.75:
                        # Check max concurrent positions
                        if len(self.open_positions) >= 2:
                            print(f"   ⚠️ Max 2 positions - skip {symbol}")
                            continue
                        
                        # Execute
                        self.execute_buy(symbol, signal)
                    
                    # Small delay between pairs
                    time.sleep(0.5)
                
                # Wait before next cycle
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
    trader.run()
