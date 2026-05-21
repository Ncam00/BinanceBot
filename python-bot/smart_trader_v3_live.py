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

import logging
import os
import time
import math
import json
from datetime import datetime
from binance.client import Client
from binance.enums import *
from binance.helpers import round_step_size
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import requests
import pytz
from utils import detect_market_regime

load_dotenv()

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/trades.log",
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)

TRADING_PAIRS         = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
TRAILING_STOP         = 0.985
RUNNER_TRAIL          = 0.970   # 3% below max_price — wide enough to let winners run
MAX_TRADES_PER_DAY    = 3
MAX_SLIPPAGE          = 0.002  # 0.2% — reject fills worse than this
MIN_VOLUME_MULTIPLIER = 1.05   # minimum volume vs avg to confirm signal (was 1.1)
POSITION_SIZE_PCT     = 0.12   # ~12% of balance per trade (~$50 on $400)
RISK_PER_TRADE        = 0.01   # 1% of balance risked per trade
TIME_EXIT_CANDLES     = 25     # exit if no progress after this many candles
PARTIAL_TP_RATIO      = 0.5    # 50% of position closes at TP1
BREAK_EVEN_BUFFER     = 0.0015
BREAKEVEN_BUFFER      = BREAK_EVEN_BUFFER  # backward-compatible alias
ATR_SL_MULTIPLIER     = 1.5    # stop loss = entry - ATR * 1.5
ATR_TP_MULTIPLIER     = 2.0    # no longer used directly; TP = sl_distance * 2
FEE_RATE              = 0.001  # 0.1% per side
SLIPPAGE_RATE         = 0.0005 # 0.05% estimated slippage
TP_FEE_BUFFER         = FEE_RATE * 2 + SLIPPAGE_RATE  # 0.0025 — adds fee cost to TP for true net 1:2
POSITION_USDT_MIN     = 55.0   # minimum position value in USDT
POSITION_USDT_MAX     = 65.0   # maximum position value in USDT
POSITION_USDT_TARGET  = 60.0   # target position value in USDT per trade
DRY_RUN               = False  # LIVE mode — real orders placed. Set True to return to paper mode.

# ─── SMOOTH MODE ────────────────────────────────────────────────────────────
# These settings reduce equity-curve volatility and remove fear-inducing swings.
A_PLUS_ONLY        = True    # RECOVERY MODE: only A+ breakouts. Skips CANDIDATE_SMALL and CANDIDATE_SCOUT.
KILL_TRADE_CANDLES = 10      # Exit losing trade after N candles of no progress (was 3)

# PHASE 2 CONFIG
ENABLE_RUNNERS     = True
TP1_MULTIPLIER     = 1.5
RUNNER_MULTIPLIER  = 4.0
EU_US_BOOST        = 1.5
ASIA_REDUCTION     = 0.7
MIN_ADD_ATR        = 0.5
# ─────────────────────────────────────────────────────────────────────────────


class EntryEngine:
    MAX_RETEST_CANDLES = 25
    TOLERANCE = 0.003  # 0.3%

    def __init__(self, pairs, execute_fn=None):
        self.pairs = pairs
        self.execute_fn = execute_fn
        self.position_open = {pair: False for pair in self.pairs}
        self.signals = {
            pair: {
                'active': False,
                'level': None,
                'direction': None,
                'retest_candles': 0,
            } for pair in self.pairs
        }

    def get(self, symbol):
        if symbol not in self.signals:
            self.signals[symbol] = {
                'active': False,
                'level': None,
                'direction': None,
                'retest_candles': 0,
            }
        return self.signals[symbol]

    def activate(self, symbol, level, direction='LONG'):
        sig = self.get(symbol)
        sig['active'] = True
        sig['level'] = level
        sig['direction'] = direction
        sig['retest_candles'] = 0

    def reset(self, symbol):
        self.signals[symbol] = {
            'active': False,
            'level': None,
            'direction': None,
            'retest_candles': 0,
        }

    def is_A_plus_setup(self, price, resistance, volume, avg_volume, close, open_price, ma):
        breakout = price > resistance
        volume_ok = volume >= avg_volume
        bullish_candle = close > open_price
        trend_ok = price > ma
        return breakout and volume_ok and bullish_candle and trend_ok

    def get_confidence(self, price, resistance, volume, avg_volume, close, open_price, ma):
        confidence = 0
        if volume >= avg_volume:
            confidence += 1
        if price > ma:          # trend_ok
            confidence += 1
        if close > open_price:  # bullish_candle
            confidence += 1
        if price > resistance:  # breakout
            confidence += 1
        return confidence

    def process_pair(self, pair, price, open_price, close, volume, avg_volume, resistance, ma, prev_close=None, atr=None, lows=None, adx=None, adx_threshold=18, atr_avg=None, bullish_timeframes=0, ema20=None, support=None, market_condition='trend', recent_volumes=None, range_high=None, is_range=False, ema9=None, ema21=None):
        sig = self.get(pair)

        # ADX filter — skip choppy markets
        if adx is not None and adx < adx_threshold:
            return {'action': 'HOLD', 'pair': pair, 'reason': f'ADX {adx:.1f} < {adx_threshold} (choppy)'}

        # Daily range context — skip longs in the top quartile of today's 24h range
        ctx = self.get_daily_context(pair)
        if ctx and ctx['price_position_pct'] > 85:
            return {'action': 'HOLD', 'pair': pair,
                    'reason': f"Near 24h high ({ctx['price_position_pct']:.0f}% of daily range)"}

        # Market mode
        market_mode = 'CHOPPY' if (atr is not None and atr_avg is not None and atr < atr_avg * 0.8) else 'ACTIVE'

        # Volume filter — skip entirely only in ACTIVE mode (CHOPPY can still scout)
        if market_mode == 'ACTIVE' and volume < avg_volume:
            return None

        # State machine: detect new breakout (volume must confirm)
        if not sig['active'] and price > resistance:
            breakout_confirmed = volume > avg_volume * 1.5
            if not breakout_confirmed:
                print(f"Skipping {pair}: unconfirmed breakout (volume {volume:.0f} < 1.5x avg {avg_volume:.0f})")
                return None
            self.activate(pair, resistance, direction='LONG')
            return {'action': 'BREAKOUT_WAIT', 'pair': pair, 'level': resistance}

        # Retest timeout
        if sig['active']:
            sig['retest_candles'] += 1
            if sig['retest_candles'] > self.MAX_RETEST_CANDLES:
                self.reset(pair)
                return {'action': 'EXPIRED', 'pair': pair}

        # ── Shared conditions ────────────────────────────────────────────────
        price_near_resistance = (resistance - price) / resistance <= 0.01
        higher_lows_forming = (
            lows is not None and len(lows) >= 3 and
            all(lows[i] >= lows[i - 1] for i in range(-min(3, len(lows)), 0))
        )
        retest = (
            sig['active'] and
            sig['direction'] == 'LONG' and
            price <= sig['level'] * (1 + self.TOLERANCE) and
            abs(price - sig['level']) / sig['level'] <= 0.005
        )
        bullish_candle  = close > open_price and (close - open_price) / open_price >= 0.001
        momentum        = prev_close is not None and close > prev_close and volume > avg_volume * 1.05
        candle_strength = atr is not None and (close - open_price) > (atr * 0.5)
        breakout_confirmed = retest and bullish_candle and momentum and candle_strength
        confidence = self.get_confidence(price, resistance, volume, avg_volume, close, open_price, ma) if sig['active'] else 0

        # ── PHASE 1: CLASSIFY ────────────────────────────────────────────────
        entry_type = None

        # BREAKOUT: range + confirmed candle close above range_high + EMA trend aligned
        if not sig['active'] and entry_type is None and not self.position_open.get(pair, False):
            trend_ok = (ema9 is not None and ema21 is not None and ema9 > ema21)
            if (is_range and range_high is not None and
                    close > range_high * 1.001 and
                    volume > avg_volume * MIN_VOLUME_MULTIPLIER and trend_ok):
                entry_type = 'BREAKOUT'

        # SCOUT_RANGE: range market + score >= 2 → small mean-reversion entry
        if not sig['active'] and entry_type is None and is_range:
            range_score = self.get_confidence(price, resistance, volume, avg_volume, close, open_price, ma)
            if range_score >= 2:
                entry_type = 'SCOUT_RANGE'

        # Squeeze: tight range + rising volume → anticipatory small entry
        if not sig['active'] and entry_type is None:
            tight_range = market_condition == 'range' or is_range
            rising_volume = (
                recent_volumes is not None and len(recent_volumes) >= 3 and
                recent_volumes[-1] > recent_volumes[-2] > recent_volumes[-3]
            )
            range_breakout = (
                range_high is not None and
                close > range_high and
                volume > avg_volume * MIN_VOLUME_MULTIPLIER
            )
            if tight_range and (rising_volume or range_breakout):
                entry_type = 'SQUEEZE'

        if not sig['active'] and price_near_resistance and higher_lows_forming:
            scout_score = sum([
                1,                              # near resistance
                1,                              # higher lows
                volume > avg_volume * 0.9,      # volume building
                price > ma,                     # above trend
            ])
            entry_type = 'SCOUT' if scout_score >= 2 else None
        elif sig['active'] and breakout_confirmed and confidence >= 4:
            entry_type = 'A+'
        elif sig['active'] and breakout_confirmed and confidence == 3:
            entry_type = 'A+'
        elif sig['active'] and breakout_confirmed and confidence == 2:
            entry_type = 'B+'
        else:
            entry_type = None

        # ── PHASE 2: FILTER ──────────────────────────────────────────────────
        # Pullback filter: only enter within 0.2% of breakout level
        if entry_type in ('A+', 'B+'):
            breakout_level = sig['level'] if sig['active'] else resistance
            if price > breakout_level * 1.002:
                print(f"Skipping {pair}: no pullback after breakout (price {price:.4f} > level {breakout_level:.4f} * 1.002)")
                entry_type = None

        if entry_type == 'A+' and bullish_timeframes < 2:
            print(f"Skipping weak breakout (MTF misaligned: {bullish_timeframes}/3 bullish)")
            entry_type = None
        elif entry_type == 'B+' and bullish_timeframes < 1:
            print(f"Skipping weak B+ setup (MTF misaligned: {bullish_timeframes}/3 bullish)")
            entry_type = None
        # SCOUT = allow regardless

        if entry_type in ('A+', 'B+') and ema20 is not None:
            trend_up = ema20 > ma
            ema_entry_ok = price <= ema20 * 1.002
            if not (trend_up and ema_entry_ok):
                print(f"Skipping {pair}: EMA entry missed (price={price:.2f} ema20={ema20:.2f} trend_up={trend_up})")
                entry_type = None

        if market_mode == 'CHOPPY' and entry_type == 'B+':
            entry_type = None

        if entry_type in ('A+', 'B+'):
            level = sig['level'] if sig['active'] else resistance
            tp_pct = 0.03 if entry_type == 'A+' else 0.0125
            tp_distance = level * tp_pct
            price_moved = price - level
            if price_moved > 0.8 * tp_distance:
                print(f"Skipping {pair}: price already moved {price_moved:.2f} > 80% of TP distance ({tp_distance:.2f})")
                entry_type = None

        # Range market: mean reversion only → block A+ breakouts
        if market_condition == 'range' and entry_type == 'A+':
            print(f"Skipping {pair}: range market — A+ breakout blocked, B+ only")
            entry_type = None

        if entry_type in ('A+', 'B+') and support is not None:
            range_size = resistance - support
            if range_size > 0:
                price_position = (price - support) / range_size
                price_in_middle_of_range = 0.3 < price_position < 0.7
                if price_in_middle_of_range:
                    print(f"Skipping {pair}: price in middle of range ({price_position:.0%} of range)")
                    entry_type = None

        # ── ACT ──────────────────────────────────────────────────────────────
        if entry_type == 'BREAKOUT':
            return {'action': 'CANDIDATE', 'pair': pair, 'level': range_high,
                    'confidence': 4, 'price': price, 'trade_type': 'BREAKOUT'}

        if entry_type == 'SCOUT_RANGE':
            return {'action': 'CANDIDATE_SCOUT', 'pair': pair, 'level': resistance,
                    'confidence': range_score, 'price': price, 'trade_type': 'SCOUT_RANGE'}

        if entry_type == 'SQUEEZE':
            return {'action': 'CANDIDATE_SCOUT', 'pair': pair, 'level': resistance,
                    'confidence': 2, 'price': price, 'trade_type': 'SCOUT'}

        if entry_type == 'SCOUT':
            return {'action': 'CANDIDATE_SCOUT', 'pair': pair, 'level': resistance,
                    'confidence': scout_score, 'price': price, 'trade_type': 'SCOUT'}

        if entry_type == 'A+':
            return {'action': 'CANDIDATE', 'pair': pair, 'level': sig['level'],
                    'confidence': confidence, 'price': price, 'trade_type': 'A+'}

        if entry_type == 'B+':
            return {'action': 'CANDIDATE_SMALL', 'pair': pair, 'level': sig['level'],
                    'confidence': confidence, 'price': price, 'trade_type': 'B+'}

        if entry_type is None and sig['active']:
            reason = (
                f'CHOPPY: B+ blocked' if market_mode == 'CHOPPY' else
                f'No structure (higher lows missing)' if not higher_lows_forming else
                f'Breakout not confirmed' if not breakout_confirmed else
                f'Low confidence ({confidence}/4)'
            )
            return {'action': 'HOLD', 'pair': pair, 'reason': reason}

        return None

    def scan_market(self, market_data):
        candidates = []
        signals = []

        # ── TWO-PHASE BREAKOUT: detect → wait for pullback → enter ───────────
        for symbol, data in market_data.items():
            if self.position_open.get(symbol, False):
                continue

            is_range   = data.get('is_range', False)
            range_high = data.get('range_high')
            close      = data.get('close', 0)
            volume     = data.get('volume', 0)
            avg_volume = data.get('avg_volume', 1)
            ema9       = data.get('ema9')
            ema21      = data.get('ema21')
            trend_ok   = ema9 is not None and ema21 is not None and ema9 > ema21

            # fetch df for signal methods
            df = self.get_candles(symbol, '15m', 60)
            if df is None or len(df) < 32:
                continue
            price      = data.get('price', close)
            top_atr    = data.get('atr')
            prev_close = data.get('prev_close', close)
            ma         = data.get('ma50', close)

            # Skip if any position already open (one at a time)
            if self.open_positions:
                continue

            # Per-symbol 15-min cooldown
            last_t = self.last_trade_time.get(symbol, 0)
            if time.time() - last_t < 900:
                continue

            # Compute entry score (trend, momentum, volume, structure)
            score = sum([
                ema9 is not None and ema21 is not None and ema9 > ema21,
                close > prev_close,
                volume > avg_volume * MIN_VOLUME_MULTIPLIER,
                price > ma,
            ])
            if score < 3:
                continue

            # Skip choppy markets — require EMA9/21 spread > 0.15% of price
            if not data.get('is_trending', False):
                print(f"   〰️ {symbol} skipped — not trending (EMA spread too tight)")
                continue

            breakout_up, _ = self.detect_breakout(df)
            vol_exp        = self.volatility_expansion(df)
            pullback_ema   = self.pullback_entry(df)

            # Pullback from last exit price — re-entry confirmation
            last_exit = self.last_exit_price.get(symbol)
            pullback_from_exit = (last_exit is not None and
                                  price < last_exit * 0.995 and breakout_up)

            # Strong breakout: breakout + volatility expansion
            if breakout_up and vol_exp:
                print(f"   🚀 STRONG BREAKOUT {symbol} @ {price:.4f}")
                self.execute_trade(symbol, price, small_position=False,
                                   atr=top_atr, trade_type='BREAKOUT_STRONG')
                candidates.append({'action': 'BUY', 'pair': symbol, 'price': price,
                                   'confidence': 5, 'trade_type': 'BREAKOUT_STRONG'})
                continue

            # Regular breakout
            if breakout_up:
                print(f"   🔴 BREAKOUT DETECTED {symbol} @ {price:.4f}")
                self.execute_trade(symbol, price, small_position=False,
                                   atr=top_atr, trade_type='BREAKOUT')
                candidates.append({'action': 'BUY', 'pair': symbol, 'price': price,
                                   'confidence': 4, 'trade_type': 'BREAKOUT'})
                continue

            # Re-entry: pullback from last exit price + breakout confirmed
            if pullback_from_exit:
                print(f"   🔁 RE-ENTRY {symbol} @ {price:.4f} (pullback from exit {last_exit:.4f})")
                self.execute_trade(symbol, price, small_position=False,
                                   atr=top_atr, trade_type='PULLBACK_ENTRY')
                candidates.append({'action': 'BUY', 'pair': symbol, 'price': price,
                                   'confidence': 4, 'trade_type': 'PULLBACK_ENTRY'})
                continue

            # Pullback into EMA9 and bounce
            if pullback_ema:
                print(f"   ↩️ PULLBACK ENTRY {symbol} @ {price:.4f}")
                self.execute_trade(symbol, price, small_position=False,
                                   atr=top_atr, trade_type='PULLBACK')
                candidates.append({'action': 'BUY', 'pair': symbol, 'price': price,
                                   'confidence': 3, 'trade_type': 'PULLBACK'})
                continue

            # Phase 1: mark range breakout level (two-phase)
            if (is_range and range_high is not None and
                    close > range_high * 1.001 and
                    volume > avg_volume * MIN_VOLUME_MULTIPLIER and trend_ok):
                if symbol not in self.breakout_levels:
                    self.breakout_levels[symbol] = range_high
                    print(f"   📌 RANGE BREAKOUT MARKED {symbol} @ {range_high:.4f} — waiting for pullback")

            # Phase 2: enter on pullback to range breakout level
            if symbol in self.breakout_levels:
                breakout_level = self.breakout_levels[symbol]
                prev_close = data.get('prev_close', close)
                ma = data.get('ma50', close)
                score = sum([
                    close > prev_close,
                    volume > avg_volume * 0.9,
                    price > ma,
                    ema9 is not None and ema21 is not None and ema9 > ema21,
                ])
                if price <= breakout_level * 1.002 and score >= 2:
                    print(f"   ✅ PULLBACK_ENTRY {symbol} @ {price:.4f} (score={score})")
                    self.execute_trade(symbol, price, small_position=False,
                                       atr=top_atr, trade_type='PULLBACK_ENTRY',
                                       range_high=breakout_level)
                    del self.breakout_levels[symbol]
                    candidates.append({'action': 'BUY', 'pair': symbol, 'price': price,
                                       'confidence': score, 'trade_type': 'PULLBACK_ENTRY'})

        for pair in self.pairs:
            data = market_data.get(pair)
            if not data:
                continue
            result = self.process_pair(
                pair,
                price=data['price'],
                open_price=data['open'],
                close=data['close'],
                volume=data['volume'],
                avg_volume=data['avg_volume'],
                resistance=data['resistance'],
                ma=data['ma50'],
                prev_close=data.get('prev_close'),
                atr=data.get('atr'),
                lows=data.get('lows'),
                adx=data.get('adx'),
                adx_threshold=18,
                atr_avg=data.get('atr_avg'),
                bullish_timeframes=data.get('bullish_timeframes', 0),
                ema20=data.get('ema20'),
                support=data.get('support'),
                market_condition=data.get('market_condition', 'trend'),
                recent_volumes=data.get('recent_volumes'),
                range_high=data.get('range_high'),
                is_range=data.get('is_range', False),
                ema9=data.get('ema9'),
                ema21=data.get('ema21'),
            )
            atr = data.get('atr', 0)
            atr_avg = data.get('atr_avg', 0)
            market_mode = 'CHOPPY' if (atr and atr_avg and atr < atr_avg * 0.8) else 'ACTIVE'
            print(f"{pair} | ATR: {atr:.2f} | AVG: {atr_avg:.2f} | MODE: {market_mode}")
            if market_mode == 'CHOPPY':
                print(f"{pair} is choppy → allowing limited trades (B+ and SCOUT only)")
            if not result:
                print(f"Skipping {pair} because: volume below average")
                continue
            if result['action'] == 'HOLD':
                print(f"Skipping {pair} because: {result.get('reason', 'HOLD')}")
                signals.append(result)
            elif result['action'] in ('CANDIDATE', 'CANDIDATE_SMALL', 'CANDIDATE_SCOUT'):
                candidates.append(result)
            else:
                signals.append(result)

        if candidates:
            if A_PLUS_ONLY:
                candidates = [c for c in candidates if c['action'] not in ('CANDIDATE_SMALL', 'CANDIDATE_SCOUT')]
            if not candidates:
                return signals
            top_pair = max(candidates, key=lambda x: x['confidence'])
            self.get(top_pair['pair'])['active'] = False
            small = top_pair['action'] == 'CANDIDATE_SMALL'
            top_atr       = market_data.get(top_pair['pair'], {}).get('atr')
            top_range_high = market_data.get(top_pair['pair'], {}).get('range_high')
            self.execute_trade(top_pair['pair'], top_pair['price'], small_position=small, atr=top_atr, trade_type=top_pair.get('trade_type', 'A+'), range_high=top_range_high)
            signals.append({**top_pair, 'action': 'BUY'})

        return signals

    def execute_trade(self, pair, price, small_position=False, atr=None, trade_type='A+', range_high=None):
        if not self.execute_fn:
            print(f"EXECUTING TRADE: {pair} at {price} ({trade_type}{'  small' if small_position else ''})")
            return
        signal = {
            'action': 'BUY',
            'price': price,
            'strength': 0.85,
            'entry_type': 'BREAKOUT',
            'support_override': self.signals[pair]['level'],
            'small_position': small_position,
            'atr': atr,
            'trade_type': trade_type,
            'range_high': range_high,
        }
        self.execute_fn(pair, signal)


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
        self.trading_pairs = TRADING_PAIRS
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
        self.daily_profit_target = 7.00       # Stop new trades at $7 profit (matches loss limit)
        self.max_daily_loss = 7.00            # Stop trading at $7 loss
        self.max_weekly_loss = 20.00          # Stop trading at $20 loss this week
        self.max_trades_per_day = MAX_TRADES_PER_DAY
        self.hard_max_trades = MAX_TRADES_PER_DAY
        self.trade_cooldown_seconds = 300     # 5 min between trades
        self.max_consecutive_losses = 4       # Pause trading after 4 losses in a row
        self.loss_streak_pause_hours = 2      # Hours to pause after hitting streak limit

        # ════════════════════════════════════════════════════════════════════
        # CIRCUIT BREAKER
        # 5% drawdown from starting balance kills the bot
        # ════════════════════════════════════════════════════════════════════
        self.starting_balance = 316.00
        self.circuit_breaker_percent = 0.05
        self.circuit_breaker_limit = self.starting_balance * (1 - self.circuit_breaker_percent)

        # ════════════════════════════════════════════════════════════════════
        # EXIT MANAGEMENT
        # ════════════════════════════════════════════════════════════════════
        self.break_even_trigger = 1.2
        self.trailing_stop_activation = 1.5
        self.trailing_stop_multiplier = TRAILING_STOP
        self.last_exit_price = {}
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
        self.adx_range_threshold = 18         # ADX < 18 = market too choppy, skip (was 22)
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
            'asia':   {'mode': 'low_risk',   'max_trades': 1, 'min_strength': 0.85, 'position_boost': 0.5, 'min_score': 3, 'allow_adds': False, 'runner_mode': False},
            'london': {'mode': 'normal',     'max_trades': 3, 'min_strength': 0.75, 'position_boost': 1.0, 'min_score': 2, 'allow_adds': True,  'runner_mode': False},
            'us':     {'mode': 'aggressive', 'max_trades': 3, 'min_strength': 0.70, 'position_boost': 1.5, 'min_score': 2, 'allow_adds': True,  'runner_mode': True},
        }

        # ════════════════════════════════════════════════════════════════════
        # STATE TRACKING (single source of truth)
        # ════════════════════════════════════════════════════════════════════
        self.daily_profit = 0.0               # SINGLE profit tracker
        self.daily_loss = 0.0                 # SINGLE loss tracker
        self.daily_loss_ratio = 0.0
        self.stats = {
            'wins':         0,
            'losses':       0,
            'total_pnl':    0.0,
            'gross_wins':   0.0,   # sum of all winning trade profits
            'gross_losses': 0.0,   # sum of all losing trade losses (absolute)
            'best_trade':   float('-inf'),
            'worst_trade':  float('inf'),
            'total_trades': 0,
        }
        self.weekly_pnl = 0.0
        self.daily_trades = 0
        self.consecutive_losses = 0
        self.pause_until = None               # time.time() timestamp when pause expires
        self.open_positions = []
        self.position_open = {}
        self.positions = {}        # symbol → {entry, sl, tp, size, remaining_size, partial_taken, tag, candles, max_price}
        self.daily_pnl = 0.0
        self.htf_cache = {}
        self.breakout_levels = {}
        self.trade_history = []
        self.entry_engine = EntryEngine(self.trading_pairs, execute_fn=self.execute_buy)
        self.trade_lock = False
        self.last_trade_time = {}
        self.last_reset_date = datetime.now().date()
        self.last_week_reset_key = self._get_week_key()
        self.daily_start_balance = None       # Set on first balance fetch of the day
        self.eu_trades_today = 0              # trades taken in EU/London session today
        self.us_trades_today = 0              # trades taken in US session today
        self.last_session = None              # track session transitions

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

    def get_daily_context(self, symbol):
        """Return price position within today's 24h range (0%=low, 100%=high)."""
        try:
            klines = self.client.get_klines(symbol=symbol, interval='1d', limit=2)
            if not klines:
                return None
            today = klines[-1]
            daily_high  = float(today[2])
            daily_low   = float(today[3])
            daily_open  = float(today[1])
            daily_close = float(today[4])
            daily_range = daily_high - daily_low
            if daily_range < 1e-8:
                return None
            current_price = float(self.client.get_symbol_ticker(symbol=symbol)['price'])
            price_position_pct = (current_price - daily_low) / daily_range * 100
            daily_trend = 'BULLISH' if daily_close > daily_open else 'BEARISH'
            return {
                'daily_high':         daily_high,
                'daily_low':          daily_low,
                'price_position_pct': price_position_pct,
                'daily_trend':        daily_trend,
            }
        except Exception as e:
            print(f"   ⚠️ Daily context error {symbol}: {e}")
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

    def get_ma(self, prices, period=50):
        return sum(prices[-period:]) / period

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
    # BREAKOUT DETECTION
    # ════════════════════════════════════════════════════════════════════
    def detect_breakout(self, df):
        recent_high = df['high'].rolling(20).max().iloc[-2]
        recent_low  = df['low'].rolling(20).min().iloc[-2]
        price       = df['close'].iloc[-1]
        return price > recent_high, price < recent_low

    def volatility_expansion(self, df):
        atr = (df['high'] - df['low']).rolling(14).mean()
        return len(atr) >= 6 and atr.iloc[-1] > atr.iloc[-5]

    def pullback_entry(self, df):
        ema       = df['close'].ewm(span=9).mean()
        price     = df['close'].iloc[-1]
        prev      = df['close'].iloc[-2]
        return price > ema.iloc[-1] and prev < ema.iloc[-2]

    def detect_range(self, df):
        if len(df) < 20:
            return False, None, None
        range_high = df['high'].rolling(20).max().iloc[-1]
        range_low  = df['low'].rolling(20).min().iloc[-1]
        price_now  = df['close'].iloc[-1]
        atr        = (df['high'] - df['low']).rolling(14).mean().iloc[-1]
        atr_avg    = (df['high'] - df['low']).rolling(28).mean().iloc[-1]
        range_size = range_high - range_low
        is_range   = (range_size < price_now * 0.03) and (atr < atr_avg)
        return is_range, range_high, range_low

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
        buy_zone_top = support + (range_size * 0.45)
        sell_zone_bottom = resistance - (range_size * 0.45)
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
    def get_higher_timeframe_levels(self, symbol):
        if not hasattr(self, 'htf_cache'):
            self.htf_cache = {}
        if symbol in self.htf_cache:
            return self.htf_cache[symbol]
        levels = {'symbol': symbol}
        for interval in ('1h', '4h'):
            df = self.get_candles(symbol, interval, 100)
            if df is None or len(df) < 50:
                continue
            sr = self.calculate_support_resistance(df)
            levels[f'{interval}_resistance'] = sr['resistance']
            levels[f'{interval}_support']    = sr['support']
        self.htf_cache[symbol] = levels
        return levels

    def count_bullish_timeframes(self, symbol, price):
        if not hasattr(self, 'htf_cache'):
            self.htf_cache = {}
        cache_key = symbol
        cached = self.htf_cache.get(cache_key)
        if cached and time.time() - cached['ts'] < 300:
            return cached['count']
        count = 0
        for interval in ('15m', '1h', '4h'):
            df = self.get_candles(symbol, interval, 55)
            if df is None or len(df) < 50:
                continue
            ma50 = df['close'].rolling(50).mean().iloc[-1]
            if price > ma50:
                count += 1
        self.htf_cache[cache_key] = {'count': count, 'ts': time.time()}
        return count

    # ════════════════════════════════════════════════════════════════════
    def get_market_data(self):
        market_data = {}
        for symbol in self.trading_pairs:
            df = self.get_candles(symbol, '15m', 100)
            if df is None or len(df) < 50:
                continue
            sr = self.calculate_support_resistance(df)
            closes = df['close'].tolist()
            volumes = df['volume'].tolist()
            adx = self.calculate_adx(df)
            atr_series = df['high'] - df['low']   # simplified ATR proxy for avg
            bb = self.calculate_bollinger(df['close'])
            atr_val  = adx['atr']
            atr_avg  = atr_series.rolling(14).mean().iloc[-1]
            bb_width = bb['upper'] - bb['lower']
            price_now = df['close'].iloc[-1]
            bb_width_small = bb_width < price_now * 0.02   # BB < 2% of price
            range_high    = df['high'].rolling(20).max().iloc[-1]
            range_low     = df['low'].rolling(20).min().iloc[-1]
            range_size    = range_high - range_low
            volatility_low = atr_val < atr_avg
            is_range      = (range_size < price_now * 0.03) and volatility_low
            market_condition = 'range' if (atr_val < atr_avg * 0.8 and bb_width_small) else 'trend'
            market_data[symbol] = {
                'price':      df['close'].iloc[-1],
                'open':       df['open'].iloc[-1],
                'high':       df['high'].iloc[-1],
                'low':        df['low'].iloc[-1],
                'close':      df['close'].iloc[-1],
                'volume':     volumes[-1],
                'avg_volume': sum(volumes[-20:-1]) / 19,
                'prev_close': df['close'].iloc[-2],
                'resistance': sr['resistance'],
                'support':    sr['support'],
                'ma50':       self.get_ma(closes, 50),
                'ema20':      self.calculate_ema(df['close'], 20),
                'ema9':       df['close'].ewm(span=9).mean().iloc[-1],
                'ema21':      df['close'].ewm(span=21).mean().iloc[-1],
                'is_trending': abs(df['close'].ewm(span=9).mean().iloc[-1] -
                                   df['close'].ewm(span=21).mean().iloc[-1]) > price_now * 0.0015,
                'adx':        adx['adx'],
                'atr':               atr_val,
                'atr_avg':           atr_avg,
                'lows':              df['low'].tolist()[-5:],
                'recent_volumes':    volumes[-5:],
                'bullish_timeframes': self.count_bullish_timeframes(symbol, price_now),
                'market_condition':  market_condition,
                'range_high':        range_high,
                'is_range':          is_range,
            }
        return market_data

    def market_is_valid(self, data):
        if data['atr'] < data['atr_avg'] * 0.8:
            return 'CHOPPY'
        if data['volume'] < data['avg_volume']:
            return 'CHOPPY'
        return 'ACTIVE'

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
    def generate_signal(self, data):
        ema20       = data['ema20']
        ema50       = data['ema50']
        rsi         = data['rsi']
        volume      = data['volume']
        avg_volume  = data['avg_volume']

        trend_up    = ema20 > ema50
        trend_down  = ema20 < ema50
        rsi_bullish = 45 < rsi < 60
        volume_spike = volume > avg_volume * 1.5

        if trend_up and rsi_bullish and volume_spike:
            return 'LONG'
        if trend_down and rsi < 45 and volume_spike:
            return 'SHORT'
        return 'NONE'

    def get_ema_pullback_signal(self, price, ema20, ema_trend, rsi, volume, avg_volume):
        trend_ok = ema20 > ema_trend                         # EMA20 > EMA50
        pullback = abs(price - ema20) / ema20 <= 0.005      # price within 0.5% of EMA20
        rsi_bounce = 45 <= rsi <= 55                         # RSI in bounce zone
        volume_spike = volume >= avg_volume

        if trend_ok and pullback and rsi_bounce and volume_spike:
            return {
                'action': 'BUY', 'strength': 0.85,
                'reason': f'EMA20 PULLBACK: price={price:.4f} ema20={ema20:.4f} rsi={rsi:.1f}',
                'entry_type': 'EMA_PULLBACK',
            }
        return {'action': 'HOLD', 'strength': 0, 'reason': 'EMA pullback: conditions not met'}

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

    def htf_trend_bullish(self, symbol):
        """1h trend filter — only enter longs when higher-timeframe trend is up.
        Returns (is_bullish, reason). Bullish = EMA20 > EMA50 on 1h AND price > EMA20."""
        try:
            df = self.get_candles(symbol, '1h', 60)
            if df is None or len(df) < 50:
                return True, 'insufficient 1h data — allowing'
            close = df['close']
            ema20_1h = close.ewm(span=20).mean().iloc[-1]
            ema50_1h = close.ewm(span=50).mean().iloc[-1]
            price = close.iloc[-1]
            bullish = ema20_1h > ema50_1h and price > ema20_1h
            if bullish:
                return True, f'1h bullish (EMA20 {ema20_1h:.2f} > EMA50 {ema50_1h:.2f})'
            return False, f'1h bearish/flat (EMA20 {ema20_1h:.2f} vs EMA50 {ema50_1h:.2f})'
        except Exception as e:
            print(f"   ⚠️ 1h trend check error ({symbol}): {e}")
            return True, 'error — allowing'

    def check_signal_layers(self, price, ema20, ema_trend, ema200, rsi, macd,
                             closes, df, adx):
        volumes = df['volume'].tolist()
        avg_volume = sum(volumes[-20:-1]) / 19

        # Trend layer: EMA20 > EMA50 > EMA200
        trend = ema20 > ema_trend > ema200 and price > ema200

        # Momentum layer: RSI above midpoint, MACD histogram positive, price accelerating
        velocity = closes.iloc[-1] - closes.iloc[-4]   # 3-candle price change
        momentum = rsi > 50 and macd['histogram'] > 0 and velocity > 0

        # Volume layer: current candle above 20-period average
        volume = volumes[-1] >= avg_volume

        # Volatility layer: ATR above 80% of threshold (avoid chop)
        volatility = adx['atr'] >= price * self.min_atr_percent * 0.8

        all_aligned = trend and momentum and volume and volatility
        return {
            'trend':      trend,
            'momentum':   momentum,
            'volume':     volume,
            'volatility': volatility,
            'all_aligned': all_aligned,
        }

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
        return self.entry_engine.get(symbol)

    def reset_breakout_state(self, symbol):
        self.entry_engine.reset(symbol)

    # ════════════════════════════════════════════════════════════════════
    # MAIN ANALYSIS (LOCATION-BASED)
    # ════════════════════════════════════════════════════════════════════
    def analyze(self, symbol):
        # Stop scanning for new trades if daily target hit
        if self.daily_profit >= self.daily_profit_target:
            return {'action': 'HOLD', 'strength': 0,
                    'reason': f'Daily target ${self.daily_profit_target} hit - no new trades'}

        df = self.get_candles(symbol, '15m', 250)
        if df is None or len(df) < 200:
            return {'action': 'HOLD', 'strength': 0, 'reason': 'Insufficient data'}

        closes = df['close']
        price = closes.iloc[-1]

        # Indicators
        rsi = self.calculate_rsi(closes)
        macd = self.calculate_macd(closes)
        ema_fast = self.calculate_ema(closes, 7)
        ema_slow = self.calculate_ema(closes, 18)
        ema20 = self.calculate_ema(closes, 20)
        ema_trend = self.calculate_ema(closes, 50)
        ema200 = self.calculate_ema(closes, 200)
        ma50 = self.get_ma(closes.tolist(), 50)
        adx = self.calculate_adx(df)
        bb = self.calculate_bollinger(closes)

        # Support/Resistance
        sr = self.calculate_support_resistance(df)
        support = sr['support']
        resistance = sr['resistance']

        market_type = self.get_market_type(adx['adx'])
        state = self.entry_engine.get(symbol)

        # ── Breakout state machine ────────────────────────────────────
        if market_type == 'TREND' and price > resistance and not state['active']:
            volumes = df['volume'].tolist()
            avg_volume = sum(volumes[-20:-1]) / 19
            breakout_volume_ok = volumes[-1] >= avg_volume * 1.5

            candle_open = df['open'].iloc[-1]
            candle_high = df['high'].iloc[-1]
            candle_range = candle_high - candle_open if candle_high > candle_open else 1e-9
            strong_close = price > candle_open and (price - candle_open) / candle_range >= 0.6

            if not breakout_volume_ok or not strong_close:
                return {
                    'action': 'HOLD', 'strength': 0,
                    'reason': '🚫 Breakout rejected: '
                              + ('low volume' if not breakout_volume_ok else 'weak close'),
                    'market_type': market_type, 'price': price,
                    'support': support, 'resistance': resistance,
                    'rsi': rsi, 'adx': adx['adx'], 'zone': 'breakout_weak'
                }

            self.entry_engine.activate(symbol, resistance, direction='LONG')
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

        if state['active']:
            state['retest_candles'] += 1
            if state['retest_candles'] > EntryEngine.MAX_RETEST_CANDLES:
                self.reset_breakout_state(symbol)
                return {
                    'action': 'HOLD', 'strength': 0,
                    'reason': f'⏳ Breakout retest expired ({EntryEngine.MAX_RETEST_CANDLES} candles)',
                    'market_type': market_type, 'price': price,
                    'support': support, 'resistance': resistance,
                    'rsi': rsi, 'adx': adx['adx'], 'zone': 'breakout_timeout'
                }

            retest_hit = (
                state['direction'] == 'LONG' and
                price <= state['level'] * (1 + EntryEngine.TOLERANCE)
            )
            if retest_hit:
                current_open = df['open'].iloc[-1]
                current_close = df['close'].iloc[-1]
                if current_close > current_open and rsi > 50:
                    state['active'] = False
                    signal = {
                        'action': 'BUY', 'strength': 0.80,
                        'reason': f"BREAKOUT BUY: Retest confirmed @ ${state['level']:.4f}",
                        'entry_type': 'BREAKOUT',
                        'support_override': state['level'],
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
                    'reason': f"⏳ Watching retest at ${state['level']:.4f}",
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
        volumes = df['volume'].tolist()
        avg_volume = sum(volumes[-20:-1]) / 19

        pullback_signal = self.get_ema_pullback_signal(
            price, ema20, ema_trend, rsi, volumes[-1], avg_volume
        )
        if pullback_signal['action'] == 'BUY':
            signal = pullback_signal
        elif market_type == 'RANGE':
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
            # Market validity gate
            volumes = df['volume'].tolist()
            atr_series = df['high'] - df['low']
            market_snapshot = {
                'atr':        adx['atr'],
                'atr_avg':    atr_series.rolling(14).mean().iloc[-1],
                'volume':     volumes[-1],
                'avg_volume': sum(volumes[-20:-1]) / 19,
            }
            market_mode = self.market_is_valid(market_snapshot)
            if market_mode == 'CHOPPY':
                return {'action': 'HOLD', 'strength': 0,
                        'reason': '🚫 Market CHOPPY: B+/SCOUT only via EntryEngine'}
            # Trend filter
            layers = self.check_signal_layers(
                price, ema20, ema_trend, ema200, rsi, macd, closes, df, adx
            )
            if not layers['all_aligned']:
                failed = [k for k, v in layers.items() if k != 'all_aligned' and not v]
                return {'action': 'HOLD', 'strength': 0,
                        'reason': f'🔲 Layers not aligned: {", ".join(failed)}'}
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
    # EXECUTE TRADE (limit order primitive)
    # ════════════════════════════════════════════════════════════════════
    def execute_trade(self, symbol, side, quantity, price):
        try:
            order = self.client.create_order(
                symbol=symbol,
                side=side,
                type='LIMIT',
                timeInForce='GTC',
                quantity=quantity,
                price=str(price),
            )
            return order
        except Exception as e:
            print(f"   ❌ Execution error ({symbol} {side}): {e}")
            return None

    # ════════════════════════════════════════════════════════════════════
    # EXECUTE BUY
    # ════════════════════════════════════════════════════════════════════
    def execute_buy(self, symbol, signal):
        if self.trade_lock:
            print(f"   🔒 TRADE LOCK - skipping duplicate {symbol}")
            return None

        # Never re-enter if any position is already open
        if self.open_positions:
            print(f"   ⛔ Position already open — skipping {symbol}")
            return None

        # Daily trade cap
        if self.daily_trades >= MAX_TRADES_PER_DAY:
            print(f"   🛑 Max trades reached ({self.daily_trades}/{MAX_TRADES_PER_DAY})")
            return None

        # Global 15-minute cooldown (any symbol)
        now = time.time()
        last_any = max(self.last_trade_time.values()) if self.last_trade_time else 0
        if now - last_any < 900:
            remaining = 900 - (now - last_any)
            print(f"   ⏳ Global cooldown: {remaining:.0f}s remaining")
            return None

        # Per-symbol 15-minute cooldown
        if symbol in self.last_trade_time and now - self.last_trade_time[symbol] < 900:
            remaining = 900 - (now - self.last_trade_time[symbol])
            print(f"   ⏳ {symbol} cooldown: {remaining:.0f}s remaining")
            return None

        self.trade_lock = True
        try:
            strong_setup = signal.get('strength', 0) >= self.strong_setup_threshold

            # EU session: only take A+ — the single EU slot is too valuable for marginal setups
            _entry_session, _ = self.get_market_session()
            if _entry_session == 'london' and not strong_setup:
                print(f"   🕐 EU session — A+ required, skipping {signal.get('trade_type', '?')} setup")
                return None

            # Trade frequency gate: free rein for first 2 trades; 3rd only on A+
            if self.daily_trades >= 2 and not strong_setup:
                print(f"   🛑 Trade #{self.daily_trades + 1} blocked - A+ setup required")
                return None

            balance = self.get_balance()
            price = signal['price']
            entry_time = datetime.now()

            # Stop loss: structure-based, never more than 3%
            support = signal.get('support_override', signal.get('support', price * 0.985))
            structure_sl = support * 0.995
            max_sl = price * 0.97
            stop_loss_price = max(structure_sl, max_sl)

            # Position size scales with trade quality
            trade_type = signal.get('trade_type', 'A+')
            if trade_type in ('B+',):
                usdt_target = POSITION_USDT_TARGET * 0.60   # 60% size for B+ setups
            elif trade_type in ('SCOUT', 'SCOUT_RANGE'):
                usdt_target = POSITION_USDT_TARGET * 0.40   # 40% size for scout entries
            else:
                usdt_target = POSITION_USDT_TARGET           # full size for A+ / BREAKOUT

            # Session-aware sizing driven by per-session position_boost
            _current_session, _sess_buy = self.get_market_session()
            _session_boost = _sess_buy.get('position_boost', 1.0)
            usdt_target *= _session_boost
            print(f"   📍 {_current_session.upper()} session boost {_session_boost:.2f}x → ${usdt_target:.2f}")

            # Correlation guard: BTC and ETH move together — halve size if the other is already open
            _correlated = {'BTCUSDT': 'ETHUSDT', 'ETHUSDT': 'BTCUSDT'}.get(symbol)
            if _correlated and _correlated in self.positions:
                usdt_target *= 0.5
                print(f"   🔗 Correlation guard — {_correlated} open, halving {symbol} to ${usdt_target:.2f}")

            # Regime adaptation from run-loop context
            position_boost = signal.get('position_boost', 1.0)
            usdt_target *= position_boost
            if position_boost != 1.0:
                print(f"   🧠 Regime boost applied — size ${usdt_target:.2f} ({position_boost:.2f}x)")

            quantity = usdt_target / price
            position_value = quantity * price
            print(f"   📐 {trade_type} | Size: ${position_value:.2f} USDT | Qty: {quantity:.5f}")

            # Pre-order slippage check: current price vs signal price
            signal_price = signal.get('price', price)
            pre_slippage = abs(price - signal_price) / signal_price
            if pre_slippage > MAX_SLIPPAGE:
                print(f"   ⚠️ {symbol} SKIP — slippage too high ({pre_slippage:.3%} > {MAX_SLIPPAGE:.3%})")
                return None

            step_size, precision = self.get_symbol_precision(symbol)
            quantity = round(quantity, precision)

            if DRY_RUN:
                fill_price = price
                entry_fee  = fill_price * quantity * FEE_RATE
                print(f"   🧪 [DRY RUN] Simulated BUY {symbol} @ ${fill_price:.4f} | Qty: {quantity} | Fee: ${entry_fee:.4f}")
                msg = f"🧪 [DRY RUN] WOULD BUY {symbol} @ ${fill_price:.4f} | Qty: {quantity:.5f}"
                self.send_telegram(msg)
            else:
                order = self.client.create_order(
                    symbol=symbol,
                    side=SIDE_BUY,
                    type=ORDER_TYPE_MARKET,
                    quantity=self.qty_to_str(symbol, quantity)
                )
                fill_price = float(order['fills'][0]['price'])
                slippage = abs(fill_price - price) / price
                if slippage > MAX_SLIPPAGE:
                    print(f"   ⚠️ {symbol} fill rejected — slippage {slippage:.3%} > MAX {MAX_SLIPPAGE:.3%}")
                    return None
                entry_fee = self.calculate_order_fee_usdt(order, symbol, fallback_price=fill_price)

            tp_multiplier = signal.get('atr_tp', RUNNER_MULTIPLIER)
            if 'tp_percent_override' in signal:
                take_profit = fill_price * (1 + signal['tp_percent_override'] / 100)
                stop_loss   = fill_price * (1 - signal['sl_percent_override'] / 100)
            elif signal.get('atr'):
                atr_val     = signal['atr']
                sl_distance = atr_val * ATR_SL_MULTIPLIER
                if sl_distance < fill_price * 0.003:
                    print(f"   ⚠️ {symbol} SL too tight ({sl_distance/fill_price:.3%}) — skipping")
                    return None
                stop_loss = fill_price - sl_distance
                print(f"   🎯 Regime TP multiplier: {tp_multiplier:.2f}x")
                take_profit = fill_price + (sl_distance * tp_multiplier) + (fill_price * TP_FEE_BUFFER)
            else:
                take_profit, stop_loss = self.set_tp_sl(
                    fill_price,
                    score=signal.get('confidence'),
                    strong_trend=strong_setup,
                    trade_type=trade_type,
                )
            actual_risk = fill_price - stop_loss
            rr_target = round((take_profit - fill_price) / max(actual_risk, 1e-9), 2)
            risk_percent = actual_risk / fill_price if fill_price else 0.0

            position = {
                'trade_id': f"{symbol}-{int(entry_time.timestamp())}",
                'symbol': symbol,
                'quantity': quantity,
                'original_quantity': quantity,
                'remaining_size': quantity,
                'entry_price': fill_price,
                'entry_resistance': signal.get('resistance', fill_price),
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
                'position_type': trade_type,
                'added': False,
                'range_high': signal.get('range_high'),
                # ── diagnostic context captured at entry for weekly review ──
                'session_at_entry': _current_session,
                'htf_bullish_at_entry': signal.get('htf_bullish'),
                'tp1': fill_price + (take_profit - fill_price) * 0.5,
                'tp2': fill_price * 1.020,
                'tp3': fill_price * 1.025,
                'tp1_hit': False,
                'tp2_hit': False,
                'runner_trailing': 0.0,
                'partial_taken': False,
                'runner_active': False,
                'candle_count': 0,
                'be_active': False,
                'trailing_stop_active': False,
                'highest_price': fill_price,
                'trailing_stop_price': None,
                'atr': signal.get('atr'),
                'timestamp': datetime.now(),
                'signal': signal
            }

            self.open_positions.append(position)
            self.position_open[symbol] = True
            self.last_trade_time[symbol] = time.time()
            _sl_distance = fill_price - stop_loss
            _atr_for_targets = signal.get('atr', _sl_distance)
            self.positions[symbol] = {
                'entry':      fill_price,
                'qty':        quantity,
                'initial_qty': quantity,
                'sl':         stop_loss,
                'tp1':        fill_price + (_atr_for_targets * TP1_MULTIPLIER),
                'runner_tp':  fill_price + (_atr_for_targets * tp_multiplier),
                'max_price':  fill_price,
                'candles':    0,
                'added':      False,
                'tp1_hit':    False,
                'entry_time': datetime.now(),
            }
            self.daily_trades += 1
            _trade_session, _ = self.get_market_session()
            if _trade_session == 'london':
                self.eu_trades_today += 1
            elif _trade_session == 'us':
                self.us_trades_today += 1

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
            logging.info(f"ENTER {symbol} | Entry: {fill_price} | SL: {stop_loss} | TP: {take_profit} | Size: {quantity}")

            return position

        except Exception as e:
            print(f"   ❌ Buy failed: {e}")
            return None
        finally:
            self.trade_lock = False

    # ════════════════════════════════════════════════════════════════════
    # EXECUTE SELL
    # ════════════════════════════════════════════════════════════════════
    def format_quantity(self, symbol, qty):
        try:
            _, precision = self.get_symbol_precision(symbol)
        except Exception:
            precision = 5
        # Floor to step precision so we never round up over actual balance
        factor = 10 ** precision
        floored = math.floor(qty * factor) / factor
        return floored

    def qty_to_str(self, symbol, qty):
        try:
            _, precision = self.get_symbol_precision(symbol)
        except Exception:
            precision = 5
        return f"{qty:.{precision}f}"

    def get_available_quantity(self, symbol):
        asset = symbol.replace('USDT', '')
        balance = self.client.get_asset_balance(asset=asset)
        if balance is None:
            return 0
        return float(balance['free'])

    def execute_sell(self, position, reason='SIGNAL', quantity=None):
        try:
            symbol = position['symbol']
            if quantity is None:
                free_balance = self.get_available_quantity(symbol)
                sell_quantity = free_balance * 0.999
            else:
                sell_quantity = quantity * 0.999
            exit_time = datetime.now()
            sell_quantity = self.format_quantity(symbol, sell_quantity)

            if sell_quantity <= 0:
                print(f"   ⚠️ Sell quantity too small for {symbol}")
                return None

            if DRY_RUN:
                fill_price = self.get_price(symbol) or position['entry_price']
                exit_fee   = fill_price * sell_quantity * FEE_RATE
                print(f"   🧪 [DRY RUN] Simulated SELL {symbol} @ ${fill_price:.4f} | Qty: {sell_quantity} | Reason: {reason}")
            else:
                order = self.client.order_market_sell(
                    symbol=symbol,
                    quantity=self.qty_to_str(symbol, sell_quantity)
                )
                fill_price = float(order['fills'][0]['price'])
                exit_fee = self.calculate_order_fee_usdt(order, symbol, fallback_price=fill_price)
            pnl = (fill_price - position['entry_price']) * sell_quantity
            pnl_percent = ((fill_price / position['entry_price']) - 1) * 100
            total_trade_pnl = position.get('realized_pnl', 0.0) + pnl
            position['realized_pnl'] = total_trade_pnl

            # Update single profit/loss tracker
            self.stats['total_trades'] += 1
            self.stats['total_pnl']    += pnl
            if pnl > 0:
                self.daily_profit += pnl
                self.stats['wins'] += 1
            else:
                self.daily_loss += abs(pnl)
                self.stats['losses'] += 1
            self.stats['best_trade']  = max(self.stats['best_trade'], pnl)
            self.stats['worst_trade'] = min(self.stats['worst_trade'], pnl)
            self.weekly_pnl += pnl
            print(f"\n   📊 STATS UPDATE"
                  f"\n   Win Rate:  {self.get_win_rate():.2f}%"
                  f"\n   Total PnL: ${self.stats['total_pnl']:.2f}"
                  f"\n   Trades:    {self.stats['total_trades']}"
                  f"\n   Best:      ${self.stats['best_trade']:.2f}"
                  f"\n   Worst:     ${self.stats['worst_trade']:.2f}")

            # Remove or reduce position
            qty_precision = 4 if symbol == 'ETHUSDT' else 5
            remaining_quantity = round(position['quantity'] - sell_quantity, qty_precision)
            if remaining_quantity <= 0:
                self.open_positions = [p for p in self.open_positions
                                       if p['trade_id'] != position['trade_id']]
                self.position_open[symbol] = False
                self.last_exit_price[symbol] = fill_price
                self.positions.pop(symbol, None)
            else:
                position['quantity'] = remaining_quantity

            # Risk controls
            balance = self.get_balance()
            if remaining_quantity <= 0:
                safe_balance = max(balance, 1e-9)
                if pnl < 0:
                    self.daily_loss_ratio += abs(pnl) / safe_balance
                result = 'LOSS' if pnl < 0 else 'WIN'
                self.log_trade(result)
                self.update_streak(result)

            # Log trade
            _exit_session, _ = self.get_market_session()
            _pos_dict = self.positions.get(symbol, {})
            self._log_trade({
                'id': position.get('trade_id'),
                'pair': symbol,
                'entry_price': position['entry_price'],
                'exit_price': fill_price,
                'position_size': sell_quantity,
                'stop_loss': position['stop_loss'],
                'take_profit': position['take_profit'],
                'profit': round(pnl, 4),
                'pnl_percent': round(pnl_percent, 4),
                'win': pnl > 0,
                'entry_time': position.get('entry_time').isoformat() if position.get('entry_time') else None,
                'exit_time': exit_time.isoformat(),
                'exit_reason': reason,
                'market_condition': position.get('market_condition'),
                'entry_reason': position.get('entry_reason'),
                # ── diagnostic fields for weekly review ─────────────────
                'trade_type': position.get('signal', {}).get('trade_type'),
                'entry_session': position.get('session_at_entry'),
                'exit_session': _exit_session,
                'htf_bullish_at_entry': position.get('htf_bullish_at_entry'),
                'atr_at_entry': position.get('atr'),
                'position_boost': position.get('signal', {}).get('position_boost'),
                'strength': position.get('signal', {}).get('strength'),
                'hold_minutes': round((exit_time - position.get('entry_time')).total_seconds() / 60.0, 1)
                                if position.get('entry_time') else None,
                'partial_taken': _pos_dict.get('tp1_hit', False),
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
            if 'insufficient balance' in str(e).lower():
                print(f"   ⚠️ {symbol} insufficient balance — forcing position reset")
                self.open_positions = [p for p in self.open_positions
                                       if p['trade_id'] != position['trade_id']]
                self.position_open[symbol] = False
                self.positions.pop(symbol, None)
            else:
                print(f"   ❌ Sell failed: {e}")
            return None

    def safe_exit(self, position, reason='SIGNAL'):
        result = self.execute_sell(position, reason)
        if result is None:
            print(f"   ⚠️ Retrying sell for {position['symbol']}...")
            time.sleep(2)
            result = self.execute_sell(position, reason)
        return result

    def calculate_profit(self, entry, price, size):
        gross    = (price - entry) * size
        fees     = (entry * size * FEE_RATE) + (price * size * FEE_RATE)
        slippage = price * size * SLIPPAGE_RATE
        return gross - fees - slippage

    def log_trade(self, result):
        self.trade_history.append(result)

    def win_rate(self):
        if not self.trade_history:
            return 0
        wins = self.trade_history.count('WIN')
        return wins / len(self.trade_history)

    def get_win_rate(self):
        total = self.stats['wins'] + self.stats['losses']
        return (self.stats['wins'] / total * 100) if total > 0 else 0

    def print_stats(self):
        s = self.stats
        total = s['wins'] + s['losses']
        if total == 0:
            print("   📊 No completed trades yet.")
            return
        win_rate  = s['wins'] / total
        avg_win   = s['gross_wins']   / s['wins']   if s['wins']   > 0 else 0.0
        avg_loss  = s['gross_losses'] / s['losses'] if s['losses'] > 0 else 0.0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        line = "─" * 36
        msg = (
            f"\n   📊 LIVE STATS ({total} trades)\n"
            f"   {line}\n"
            f"   Win Rate:   {win_rate * 100:.1f}%  ({s['wins']}W / {s['losses']}L)\n"
            f"   Avg Win:    ${avg_win:.2f}\n"
            f"   Avg Loss:   ${avg_loss:.2f}\n"
            f"   Expectancy: ${expectancy:.2f} per trade\n"
            f"   Total PnL:  ${s['total_pnl']:.2f}\n"
            f"   Best:       ${s['best_trade']:.2f}\n"
            f"   Worst:      ${s['worst_trade']:.2f}\n"
            f"   {line}"
        )
        print(msg)
        logging.info(
            f"STATS | Trades: {total} | WR: {win_rate*100:.1f}% | "
            f"AvgWin: ${avg_win:.2f} | AvgLoss: ${avg_loss:.2f} | "
            f"Expectancy: ${expectancy:.2f} | TotalPnL: ${s['total_pnl']:.2f}"
        )
        self.send_telegram(
            f"📊 Stats ({total} trades)\n"
            f"Win Rate: {win_rate*100:.1f}% ({s['wins']}W/{s['losses']}L)\n"
            f"Avg Win: ${avg_win:.2f} | Avg Loss: ${avg_loss:.2f}\n"
            f"Expectancy: ${expectancy:.2f}/trade\n"
            f"Total PnL: ${s['total_pnl']:.2f}"
        )

    def _log_trade(self, trade_data):
        log_path = os.path.join(os.path.dirname(__file__), 'trade_log.jsonl')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(trade_data, ensure_ascii=True) + "\n")

    # ════════════════════════════════════════════════════════════════════
    # UNIFIED ENTRY CHECK
    # ════════════════════════════════════════════════════════════════════
    def check_entry(self, symbol, df):
        # ── HARD GUARDS ──────────────────────────────────────────────────
        if self.open_positions:
            return
        if self.daily_trades >= MAX_TRADES_PER_DAY:
            return
        # Session-restricted entries: only London open & US open windows (UTC)
        from datetime import timezone as _tz
        _utc_now = datetime.now(_tz.utc)
        utc_time = _utc_now.hour + _utc_now.minute / 60.0
        in_london = 7.0 <= utc_time < 11.0       # London open + first hours
        in_us     = 13.0 <= utc_time < 17.0      # US open through power hour
        if not (in_london or in_us):
            return  # silent skip — outside high-conviction windows
        last_any = max(self.last_trade_time.values()) if self.last_trade_time else 0
        if time.time() - last_any < 900:
            return
        # prevent BTC + ETH double exposure
        if ('BTCUSDT' in self.positions and symbol == 'ETHUSDT') or \
           ('ETHUSDT' in self.positions and symbol == 'BTCUSDT'):
            return

        # ── CORE DATA ────────────────────────────────────────────────────
        close  = df['close']
        high   = df['high']
        low    = df['low']
        volume = df['volume']
        price  = close.iloc[-1]

        # ── TREND ────────────────────────────────────────────────────────
        ema_fast = close.ewm(span=9).mean()
        ema_slow = close.ewm(span=21).mean()
        trend    = ema_fast.iloc[-1] > ema_slow.iloc[-1]

        # ── MOMENTUM ALIGNMENT: RSI rising + MACD cross ──────────────────
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = 100 - (100 / (1 + rs))
        rsi_rising = rsi.iloc[-1] > 50 and rsi.iloc[-1] > rsi.iloc[-3]

        ema12       = close.ewm(span=12).mean()
        ema26       = close.ewm(span=26).mean()
        macd_line   = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_cross  = macd_line.iloc[-1] > signal_line.iloc[-1] and macd_line.iloc[-2] <= signal_line.iloc[-2]

        momentum = rsi_rising  # RSI rising strongly (used in 3-factor score)

        # ── VOLUME (session-aware floor) ─────────────────────────────────
        _nz_h = datetime.now(pytz.timezone('Pacific/Auckland')).hour
        if 1 <= _nz_h <= 5:        # US session — require stronger confirmation
            _vol_min = 1.5
        elif 19 <= _nz_h <= 23:    # EU session
            _vol_min = 1.3
        else:
            _vol_min = MIN_VOLUME_MULTIPLIER  # 1.1 fallback
        avg_vol      = volume.rolling(20).mean().iloc[-1]
        volume_spike = volume.iloc[-1] > avg_vol * _vol_min

        # ── BREAKOUT ─────────────────────────────────────────────────────
        recent_high = high.rolling(10).max().iloc[-2]
        breakout    = price > recent_high

        # ── VOLATILITY EXPANSION ─────────────────────────────────────────
        atr                  = (high - low).rolling(14).mean()
        atr_val              = atr.iloc[-1]
        if atr_val < price * 0.002:   # require 0.2% ATR for calmer market entries
            return
        volatility_expanding = len(atr) >= 6 and atr_val > atr.iloc[-5]

        # ── MOMENTUM ALIGNMENT SCORE (3-factor: trend / momentum / volume) ──
        score = sum([trend, momentum, volume_spike])  # max 3 = A+ conditions

        _entry_sess, _entry_settings = self.get_market_session()
        _min_score = _entry_settings.get('min_score', 2)

        # ── ENTRY ────────────────────────────────────────────────────────
        top_atr = atr.iloc[-1]
        rsi_val = rsi.iloc[-1]

        # 1h trend filter — skip counter-trend entries
        htf_ok, htf_reason = self.htf_trend_bullish(symbol)
        if not htf_ok:
            print(f"   🚫 {symbol} skipped — {htf_reason}")
            return

        # Conviction-based sizing: A+ aligned with 1h trend = full, scout = small
        # (passed to execute_buy via signal dict; execute_buy applies session_boost on top)
        if score == 3 and volatility_expanding:
            # A+ confluence + 1h aligned = max conviction
            conviction_size = 1.20 if macd_cross else 1.00
            print(f"   ⭐ A+ {symbol} score={score}/3 @ {price:.4f} (RSI:{rsi_val:.0f} MACD:{'✓' if macd_cross else '~'}) [{htf_reason}]")
            self.execute_buy(symbol, {
                'price': price,
                'trade_type': 'A+_BREAKOUT',
                'strength': 1.0,
                'atr': top_atr,
                'position_boost': conviction_size,
                'htf_bullish': htf_ok,
            })
            return
        if score >= _min_score and volatility_expanding and breakout:
            print(f"   🔍 SCOUT {symbol} score={score}/3 @ {price:.4f} (RSI:{rsi_val:.0f}) [{htf_reason}]")
            self.execute_buy(symbol, {
                'price': price,
                'trade_type': 'SCOUT',
                'strength': 0.5,
                'atr': top_atr,
                'position_boost': 0.65,
                'htf_bullish': htf_ok,
            })

    # ════════════════════════════════════════════════════════════════════
    # UNIFIED EXIT
    # ════════════════════════════════════════════════════════════════════
    def exit_trade(self, symbol, reason, price):
        pos = self.positions.get(symbol)
        if not pos:
            return
        open_pos = next((p for p in self.open_positions if p['symbol'] == symbol), None)
        if open_pos is None:
            self.positions.pop(symbol, None)
            return
        order = self.safe_exit(open_pos, reason)
        profit = self.calculate_profit(pos['entry'], price, pos['qty'])
        self.daily_pnl += profit

        # Record stats
        self.stats['total_trades'] += 1
        self.stats['total_pnl']    += profit
        self.stats['best_trade']    = max(self.stats['best_trade'], profit)
        self.stats['worst_trade']   = min(self.stats['worst_trade'], profit)
        if profit >= 0:
            self.stats['wins']       += 1
            self.stats['gross_wins'] += profit
        else:
            self.stats['losses']       += 1
            self.stats['gross_losses'] += abs(profit)

        if order is not None:
            msg = f"{reason} {symbol} | PnL: ${profit:.2f} | Daily PnL: ${self.daily_pnl:.2f}"
            logging.info(msg)
            self.send_telegram(msg)
        else:
            msg = f"SELL FAILED {symbol} | {reason}"
            logging.error(msg)
            self.send_telegram(msg)
        self.positions.pop(symbol, None)
        self.print_stats()

    # ════════════════════════════════════════════════════════════════════
    # POSITION MANAGEMENT (single unified exit system)
    # ════════════════════════════════════════════════════════════════════
    def check_positions(self):
        # ── manage_trade loop ─────────────────────────────────────────────
        for symbol, pos in list(self.positions.items()):
            current_price = self.get_price(symbol)
            if not current_price:
                continue
            if next((p for p in self.open_positions if p['symbol'] == symbol), None) is None:
                self.positions.pop(symbol, None)
                continue

            # Track candles by real elapsed time, not scan-cycle count
            pos['candles'] = int((datetime.now() - pos.get('entry_time', datetime.now())).total_seconds() / (15 * 60))

            # Fee-killer guard: skip exit if move too small to cover fees
            atr_df = self.get_candles(symbol, '15m', 20)
            atr_now = ((atr_df['high'] - atr_df['low']).rolling(14).mean().iloc[-1]
                       if atr_df is not None and len(atr_df) >= 14 else None)
            if atr_now and abs(current_price - pos['entry']) < atr_now * 0.3:
                continue

            # Momentum add only after price clears entry by MIN_ADD_ATR × ATR
            _, _add_sess = self.get_market_session()
            if _add_sess.get('allow_adds', True) and (not pos.get('added')) and atr_now and current_price > pos['entry'] + (atr_now * MIN_ADD_ATR):
                open_pos = next((p for p in self.open_positions if p['symbol'] == symbol), None)
                if open_pos is not None:
                    self.add_small_position(open_pos, current_price)
                    if open_pos.get('scaled_in'):
                        pos['qty'] = open_pos.get('quantity', pos['qty'])
                        pos['added'] = True

            # STOP LOSS
            if current_price <= pos['sl']:
                self.exit_trade(symbol, 'STOP LOSS', current_price)
                continue

            # STAGE 1: TP1 hit — sell partial, move SL to breakeven buffer
            if not pos['tp1_hit'] and current_price >= pos['tp1']:
                sell_qty = self.format_quantity(symbol, pos['qty'] * PARTIAL_TP_RATIO)
                open_pos = next((p for p in self.open_positions if p['symbol'] == symbol), None)
                order = self.execute_sell(open_pos, 'TP1', quantity=sell_qty) if open_pos else None
                if order:
                    profit = self.calculate_profit(pos['entry'], current_price, sell_qty)
                    self.daily_pnl += profit
                    self.stats['total_trades'] += 1
                    self.stats['total_pnl'] += profit
                    self.stats['wins'] += 1
                    self.stats['gross_wins'] += profit
                    self.stats['best_trade'] = max(self.stats['best_trade'], profit)
                    pos['qty'] -= sell_qty
                    pos['tp1_hit'] = True
                    pos['sl'] = pos['entry'] * (1 + BREAK_EVEN_BUFFER)
                    msg = f"TP1 HIT {symbol} | +${profit:.2f} | SL → breakeven+buffer"
                    logging.info(msg)
                    self.send_telegram(msg)
                    self.print_stats()

            # Full runner exit after TP1 (US session only)
            _, _runner_sess = self.get_market_session()
            if ENABLE_RUNNERS and _runner_sess.get('runner_mode', False) and pos.get('tp1_hit') and current_price >= pos.get('runner_tp', float('inf')):
                self.exit_trade(symbol, 'RUNNER TP', current_price)
                continue

            # TRACK MAX PRICE
            if current_price > pos['max_price']:
                pos['max_price'] = current_price

            # RUNNER: trail 3% below max after TP1; 1.5% before
            trail_pct = RUNNER_TRAIL if pos['tp1_hit'] else TRAILING_STOP
            trailing_sl = pos['max_price'] * trail_pct
            if current_price <= trailing_sl:
                self.exit_trade(symbol, 'RUNNER EXIT', current_price)
                continue

            # Smarter time exit: only exit stagnant or losing positions — never cut a winner
            if (
                pos['candles'] >= TIME_EXIT_CANDLES
                and atr_now is not None
                and current_price <= pos['entry']  # only if not profitable
                and abs(current_price - pos['entry']) > atr_now * 0.3
            ):
                self.exit_trade(symbol, 'TIME EXIT', current_price)
        # ── END manage_trade loop ─────────────────────────────────────────

        for position in self.open_positions[:]:
            symbol = position['symbol']
            current_price = self.get_price(symbol)
            if not current_price:
                continue

            pnl_percent = ((current_price - position['entry_price']) / position['entry_price']) * 100
            profit = (current_price - position['entry_price']) * position['quantity']

            # 1. STOP LOSS
            if current_price <= position['stop_loss']:
                print(f"\n   🛑 STOP LOSS {symbol} @ ${current_price:.4f}")
                self.execute_sell(position, 'STOP_LOSS')
                continue

            # 2. KILL BAD TRADES: exit if still losing after 3 candles (45 min)
            candles_open = int((datetime.now() - position['entry_time']).total_seconds() / (15 * 60))
            position['candle_count'] = candles_open

            # TIME EXIT: only exit if no progress after 30 candles
            if candles_open >= TIME_EXIT_CANDLES and current_price <= position['entry_price']:
                print(f"\n   ⏱️ TIME EXIT {symbol}: {candles_open} candles, no progress")
                self.execute_sell(position, 'TIME_EXIT')
                continue

            # Session-specific kill timer: cut EU losers fast, give US runners more room
            _monitor_session, _ = self.get_market_session()
            if _monitor_session == 'london':
                _kill_candles = 6    # ~1.5h — don't let EU losers eat the daily budget before US opens
            elif _monitor_session == 'us':
                _kill_candles = 14   # ~3.5h — US trends need room; institutional moves take time
            else:
                _kill_candles = KILL_TRADE_CANDLES
            if candles_open > _kill_candles and pnl_percent < -1.0:
                print(f"\n   ⚡ KILL BAD TRADE {symbol}: {candles_open} candles open, PNL {pnl_percent:.2f}%")
                self.execute_sell(position, 'TIMEOUT_LOSS')
                continue

            # 4. SCOUT ADD-ON: scale to full position when breakout confirms
            if position.get('position_type') == 'SCOUT':
                print(f"   [SCOUT] {symbol} | position_open: True | already_added: {position.get('added', False)}")
            if position.get('position_type') == 'SCOUT' and not position.get('added'):
                try:
                    candle = self.get_candles(symbol, '15m', 3)
                    if candle is not None and len(candle) >= 2:
                        vol     = candle['volume'].iloc[-1]
                        avg_vol = candle['volume'].iloc[:-1].mean()
                        resistance = position.get('entry_resistance', position['entry_price'] * 1.005)
                        breakout_confirmed = (
                            current_price > resistance and
                            vol > avg_vol * MIN_VOLUME_MULTIPLIER and
                            candle['close'].iloc[-1] > resistance
                        )
                        not_too_extended = current_price <= position['entry_price'] * 1.01
                        close_above_entry = candle['close'].iloc[-1] > position['entry_price']
                        if breakout_confirmed and not_too_extended and close_above_entry:
                            balance  = self.get_balance()
                            add_qty  = round((balance * 0.10) / current_price, 6)
                            if add_qty * current_price >= 10:
                                self.execute_trade(symbol, SIDE_BUY, add_qty, current_price)
                                position['quantity'] += add_qty
                                position['position_type'] = 'A+'
                                position['added'] = True
                                print(f"   ➕ SCOUT ADD-ON {symbol}: +{add_qty} @ ${current_price:.4f} → upgraded to A+")
                except Exception as e:
                    print(f"   ⚠️ Scout add-on error: {e}")

            # 5. BREAK-EVEN SHIELD: move SL to entry at 1% profit
            if pnl_percent >= self.break_even_trigger and not position.get('be_active'):
                position['stop_loss'] = position['entry_price']
                position['be_active'] = True
                print(f"   🛡️ BREAK-EVEN: {symbol} SL moved to entry ${position['entry_price']:.4f}")
                self.send_telegram(
                    f"🛡️ Break-Even Active\n{symbol}\nSL moved to entry"
                )

            # 5. RSI OVERBOUGHT EXIT: take profit when RSI rolls over from overbought
            try:
                rsi_df = self.get_candles(symbol, '15m', 20)
                if rsi_df is not None and len(rsi_df) >= 16:
                    rsi_series = (100 - (100 / (1 + (
                        rsi_df['close'].diff().where(lambda d: d > 0, 0).rolling(14).mean() /
                        (-rsi_df['close'].diff().where(lambda d: d < 0, 0)).rolling(14).mean()
                    )))).dropna()
                    if len(rsi_series) >= 2:
                        current_rsi = rsi_series.iloc[-1]
                        prev_rsi    = rsi_series.iloc[-2]
                        if prev_rsi >= 70 and current_rsi < prev_rsi and pnl_percent > 0 and position.get('tp1_hit'):
                            print(f"\n   📉 RSI OVERBOUGHT EXIT {symbol}: RSI {prev_rsi:.1f}→{current_rsi:.1f}, PNL +{pnl_percent:.2f}%")
                            self.execute_sell(position, 'RSI_OVERBOUGHT')
                            continue
            except Exception:
                pass

            # 6. TRAILING STOP: only activates after TP1 is secured; ATR-based width
            # Tracks highest price throughout the trade regardless of when trail activates
            if current_price > position.get('highest_price', 0):
                position['highest_price'] = current_price

            if position.get('tp1_hit'):
                # Use ATR for trail width so normal crypto pullbacks don't shake us out
                atr = position.get('atr')
                if atr:
                    new_trail = position['highest_price'] - atr * 1.5
                else:
                    new_trail = position['highest_price'] * 0.980  # 2% fallback

                if not position.get('trailing_stop_active'):
                    position['trailing_stop_active'] = True
                    position['trailing_stop_price'] = new_trail
                    print(f"   🔒 TRAILING STOP ACTIVATED {symbol} @ ${new_trail:.4f}")
                    self.send_telegram(
                        f"🔒 Trailing Stop Active\n{symbol}\n"
                        f"Profit: +{pnl_percent:.2f}%\n"
                        f"Trail: ${new_trail:.4f}"
                    )
                elif new_trail > position.get('trailing_stop_price', 0):
                    position['trailing_stop_price'] = new_trail
                    print(f"   📈 TRAILING STOP RAISED {symbol} @ ${new_trail:.4f}")

                if position.get('trailing_stop_price') and current_price <= position['trailing_stop_price']:
                    print(f"\n   🔒 TRAILING STOP HIT {symbol} @ ${current_price:.4f}")
                    self.execute_sell(position, 'TRAILING_STOP')
                    continue

            # 4. MOMENTUM ADD-ON: scale in only after 0.5x ATR confirmed move
            add_threshold = position['entry_price'] + (position.get('atr', 0) * MIN_ADD_ATR)
            trade_in_profit = current_price > add_threshold
            breakout_continues = current_price > position.get('entry_resistance', current_price)
            _, _add_sess2 = self.get_market_session()
            if _add_sess2.get('allow_adds', True) and trade_in_profit and breakout_continues and not position.get('scaled_in'):
                self.add_small_position(position, current_price)

            # 8. TP1: sell PARTIAL_TP_RATIO, move SL to entry + BREAKEVEN_BUFFER
            if not position.get('tp1_hit') and current_price >= position['tp1']:
                qty = position['original_quantity'] * PARTIAL_TP_RATIO
                result = self.execute_sell(position, 'TP1', quantity=qty)
                if result:
                    position['tp1_hit'] = True
                    position['remaining_size'] = position.get('remaining_size', position['original_quantity']) * (1 - PARTIAL_TP_RATIO)
                    position['stop_loss'] = position['entry_price'] * (1 + BREAKEVEN_BUFFER)
                    print(f"   🎯 TP1 {symbol} → sold {PARTIAL_TP_RATIO:.0%}, SL → breakeven+{BREAKEVEN_BUFFER:.1%}")
                continue

            # 9. TP2 (+2%): sell another 30% (80% total closed)
            if position.get('tp1_hit') and not position.get('tp2_hit') and current_price >= position['tp2']:
                qty = position['original_quantity'] * 0.3
                result = self.execute_sell(position, 'TP2', quantity=qty)
                if result:
                    position['tp2_hit'] = True
                    position['runner_trailing'] = current_price * self.trailing_stop_multiplier
                    print(f"   🎯 TP2 {symbol} +2% → sold 30%, runner trailing @ ${position['runner_trailing']:.4f}")
                continue

            # 10. RUNNER (last 20%): 2% ATR-based trail — wide enough for crypto volatility
            if position.get('tp2_hit'):
                atr = position.get('atr')
                if atr:
                    runner_trail = position['highest_price'] - atr * 1.5
                else:
                    runner_trail = current_price * 0.980  # 2% fallback
                position['runner_trailing'] = max(position.get('runner_trailing', 0), runner_trail)
                if current_price <= position['runner_trailing']:
                    print(f"\n   🏁 RUNNER EXIT {symbol} @ ${current_price:.4f}")
                    self.execute_sell(position, 'RUNNER_TRAIL')
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
            self.eu_trades_today = 0
            self.us_trades_today = 0
            self.last_session = None
            self.daily_profit = 0.0
            self.daily_loss = 0.0
            self.daily_pnl = 0.0
            self.daily_loss_ratio = 0.0
            self.consecutive_losses = 0
            self.last_trade_time = {}
            self.last_reset_date = today
            self.daily_start_balance = self.get_balance()
            self.send_telegram(
                f"🌅 New trading day\n"
                f"Start balance: ${self.daily_start_balance:.2f}\n"
                f"Target: ${self.daily_profit_target}\n"
                f"Loss limit: 5%"
            )

    # ════════════════════════════════════════════════════════════════════
    # CAN TRADE (single unified gate)
    # ════════════════════════════════════════════════════════════════════
    def add_small_position(self, position, current_price, add_pct=0.05):
        if position.get('scaled_in'):
            return
        balance = self.get_balance()
        add_qty = (balance * add_pct) / current_price
        step_size, precision = self.get_symbol_precision(position['symbol'])
        add_qty = round(add_qty, precision)
        if add_qty * current_price < 10:
            return
        try:
            self.client.create_order(
                symbol=position['symbol'],
                side='BUY',
                type='MARKET',
                quantity=add_qty,
            )
            position['quantity'] += add_qty
            position['scaled_in'] = True
            print(f"   ➕ SCALE-IN {position['symbol']} +{add_qty} @ ${current_price:.4f}")
        except Exception as e:
            print(f"   ❌ Scale-in error: {e}")

    def trailing_stop(self, current_price, entry_price):
        if current_price > entry_price * 1.01:   # price up > 1%
            return current_price * 0.995         # lock profit at 0.5% below current
        return None

    def forced_b_plus_attempt(self, market_data):
        if self.daily_trades > 0:
            return
        if not self.can_trade():
            return

        print("[FORCED B+ ATTEMPT]")

        best_pair = None
        best_score = -1
        for symbol, data in market_data.items():
            score = self.entry_engine.get_confidence(
                data['price'], data['resistance'], data['volume'], data['avg_volume'],
                data['close'], data['open'], data['ma50'],
            )
            if score > best_score:
                best_score = score
                best_pair = symbol

        if not best_pair:
            return

        data = market_data[best_pair]
        balance = self.get_balance()
        signal = {
            'action':              'BUY',
            'price':               data['price'],
            'strength':            0.85,
            'entry_type':          'FORCED_B_PLUS',
            'support_override':    data.get('support', data['price'] * 0.99),
            'confidence':          best_score,
            'tp_percent_override': 1.2,
            'sl_percent_override': 1.0,
            'atr':                 data.get('atr'),
            'forced':              True,
        }
        self.execute_buy(best_pair, signal)

    def adapt_strategy(self):
        wr = self.win_rate()
        if wr < 0.5:
            return {'tp': 1.5, 'sl': 0.8}
        elif wr > 0.6:
            return {'tp': 2.5, 'sl': 1.2}
        return {'tp': 2.0, 'sl': 1.0}

    def set_tp_sl(self, entry_price, score=None, strong_trend=False, trade_type=None):
        params = self.adapt_strategy()
        # Trade type overrides first (A+ / B+)
        if trade_type == 'A+':
            params['tp'] = 3.0   # midpoint of 2-4%
        elif trade_type == 'B+':
            params['tp'] = 1.25  # midpoint of 1-1.5%
        # Score-based fallback
        elif score is not None:
            if score >= 5:
                params['tp'] = 3.0
            elif score >= 4:
                params['tp'] = 2.5
            else:
                params['tp'] = 1.5
        # Strong trend override: let winners run longer
        elif strong_trend:
            params['tp'] = max(params['tp'], 3.0)
        tp = entry_price * (1 + params['tp'] / 100)
        sl = entry_price * (1 - params['sl'] / 100)
        return tp, sl

    def adjust_risk(self, base_risk):
        if self.consecutive_losses >= 2:
            return 0.01   # reduce to 1% after 2 losses in a row
        return base_risk  # normal risk

    def update_streak(self, result):
        if result == 'LOSS':
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        if self.consecutive_losses >= self.max_consecutive_losses:
            self.pause_until = time.time() + self.loss_streak_pause_hours * 3600
            self.send_telegram(
                f"⏸️ Loss streak ({self.consecutive_losses}) — pausing {self.loss_streak_pause_hours}h"
            )
            return False
        return True

    def check_daily_loss(self):
        if not self.daily_start_balance:
            self.daily_start_balance = self.get_balance()
        balance = self.get_balance()
        daily_loss = (balance - self.daily_start_balance) / self.daily_start_balance
        if daily_loss <= -0.05:
            return False, daily_loss
        return True, daily_loss

    def can_trade(self):
        # Trading window: 24/7 (crypto markets never close)
        # Session-slot caps below already prevent overtrading per session.

        # Weekly loss guard
        if self.weekly_pnl <= -self.max_weekly_loss:
            return False, f"🛑 WEEKLY LOSS LIMIT: ${self.weekly_pnl:.2f}"

        # Daily profit target hit
        if self.daily_profit >= self.daily_profit_target:
            return False, f"🎯 DAILY TARGET HIT: ${self.daily_profit:.2f}"

        # Daily loss limit
        if self.daily_loss >= self.max_daily_loss:
            return False, f"🔴 DAILY LOSS LIMIT: -${self.daily_loss:.2f}"

        # Balance-based daily loss check (-5% of start balance)
        loss_ok, daily_loss_pct = self.check_daily_loss()
        if not loss_ok:
            return False, f"🔴 DAILY LOSS -5%: {daily_loss_pct*100:.1f}% drawdown — stop trading"

        # Consecutive losses
        if self.pause_until:
            if time.time() < self.pause_until:
                remaining = (self.pause_until - time.time()) / 60
                return False, f"⏸️ LOSS STREAK PAUSE: {remaining:.0f}min remaining"
            self.pause_until = None
            self.consecutive_losses = 0

        if self.consecutive_losses >= self.max_consecutive_losses:
            self.pause_until = time.time() + self.loss_streak_pause_hours * 3600
            self.send_telegram(
                f"⏸️ Loss streak ({self.consecutive_losses}) — pausing {self.loss_streak_pause_hours}h"
            )
            return False, f"⏸️ LOSS STREAK: pausing {self.loss_streak_pause_hours}h"

        # Hard trade cap
        if self.daily_trades >= self.hard_max_trades:
            return False, f"🛑 MAX TRADES: {self.daily_trades}/{self.hard_max_trades}"

        # Cooldown (global: 300s since any trade)
        last_any = max(self.last_trade_time.values()) if self.last_trade_time else None
        if last_any and time.time() - last_any < 300:
            remaining = 300 - (time.time() - last_any)
            return False, f"⏳ COOLDOWN: {remaining:.0f}s remaining"

        # Per-session slot management — EU capped at 1 to guarantee 2 slots for US
        session, settings = self.get_market_session()
        if session == 'london':
            if self.eu_trades_today >= 1:
                return False, f"🕐 EU slot taken ({self.eu_trades_today}/1) — saving 2 slots for US"
            if self.daily_profit >= 3.50:
                return False, f"🕐 EU profit lock (${self.daily_profit:.2f} ≥ $3.50) — preserving daily budget for US"
        elif session == 'us':
            eu_unused = max(0, 1 - self.eu_trades_today)   # slot EU didn't use
            us_cap = 2 + eu_unused                          # US gets 2 + any EU leftover
            if self.us_trades_today >= us_cap:
                return False, f"🕐 US slots full ({self.us_trades_today}/{us_cap})"
        elif session == 'asia' and self.daily_trades >= settings['max_trades']:
            return False, f"Session limit ({self.daily_trades}/{settings['max_trades']} ASIA)"

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
                if self.position_open.get(symbol, False):
                    continue
                entry_price = known_entries.get(symbol, current_price)
                stop_loss = entry_price * (1 - self.stop_loss_percent / 100)
                take_profit = entry_price * (1 + self.take_profit_percent / 100)
                _sl_distance = entry_price - stop_loss
                tp1_price = entry_price + (_sl_distance * TP1_MULTIPLIER)
                runner_tp_price = entry_price + (_sl_distance * RUNNER_MULTIPLIER)
                position = {
                    'trade_id': f"{symbol}-synced",
                    'symbol': symbol,
                    'quantity': amount,
                    'original_quantity': amount,
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'tp1': tp1_price,
                    'runner_tp': runner_tp_price,
                    'tp1_hit': False,
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
                self.position_open[symbol] = True
                self.positions[symbol] = {
                    'entry':     entry_price,
                    'qty':       amount,
                    'initial_qty': amount,
                    'sl':        stop_loss,
                    'tp1':       tp1_price,
                    'runner_tp': runner_tp_price,
                    'max_price': current_price,
                    'candles':   0,
                    'added':     False,
                    'tp1_hit':   False,
                }
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
        if DRY_RUN:
            print("   🧪 SMART TRADER V3 - PAPER MODE (DRY RUN)")
            print("   ⚠️  NO REAL ORDERS WILL BE PLACED")
            print("   Set DRY_RUN = False in constants to go live")
            self.send_telegram("🧪 Bot started in PAPER MODE — no real orders will be placed")
        else:
            print("   🚀 SMART TRADER V3 - LIVE")
            self.send_telegram("🚀 Bot started in LIVE MODE")
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
                    regime = 'RANGING'
                    atr_tp = 2.2
                    position_boost = 1.0

                    # Skip if already in this symbol
                    if self.position_open.get(symbol, False):
                        continue

                    # Position cap re-check
                    if len(self.open_positions) >= self.max_positions:
                        break

                    # BTC filter for alts
                    if symbol not in ('BTCUSDT',) and not self.btc_is_healthy():
                        print(f"   ⚠️ {symbol} skipped - BTC filter")
                        continue

                    # Unified entry check (score-based)
                    df_entry = self.get_candles(symbol, '15m', 60)
                    if df_entry is not None and len(df_entry) >= 32:
                        regime = detect_market_regime(df_entry)
                        if regime == 'TRENDING':
                            atr_tp = 4.0
                            position_boost = 1.3
                        elif regime == 'VOLATILE':
                            atr_tp = 1.8
                            position_boost = 0.7

                        recent_move = abs(df_entry['close'].iloc[-1] - df_entry['close'].iloc[-5])
                        atr = (df_entry['high'] - df_entry['low']).rolling(14).mean().iloc[-1]
                        if atr > 0 and recent_move > atr * 2.0:
                            print(f"   ⚠️ {symbol} skipped — late entry (move {recent_move:.4f} > 2.0×ATR {atr * 2.0:.4f})")
                        else:
                            self.check_entry(symbol, df_entry)

                    signal = self.analyze(symbol)
                    signal['regime'] = regime
                    signal['atr_tp'] = atr_tp
                    signal['position_boost'] = position_boost

                    # Log every signal reason so you can see exactly what's blocking
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
                import traceback
                print(f"\n   ❌ Loop error: {e}")
                traceback.print_exc()
                time.sleep(10)

        net_pnl = self.daily_profit - self.daily_loss
        print(f"\n   📊 Session Summary:")
        print(f"      Trades: {self.daily_trades}")
        print(f"      Daily P&L: ${net_pnl:.2f}")
        print(f"      Open positions: {len(self.open_positions)}")


if __name__ == '__main__':
    trader = SmartTrader()
    trader.run()