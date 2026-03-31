"""
Trading Strategy Module
Implements RSI, MACD, and EMA indicator-based strategy
"""
import logging
from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass
from enum import Enum
import pandas as pd
import numpy as np

try:
    import ta
    from ta.momentum import RSIIndicator
    from ta.trend import MACD, EMAIndicator
    from ta.volume import VolumeWeightedAveragePrice
except ImportError:
    ta = None

import config

logger = logging.getLogger(__name__)


class Signal(Enum):
    """Trading signal types"""
    STRONG_BUY = 2
    BUY = 1
    HOLD = 0
    SELL = -1
    STRONG_SELL = -2


@dataclass
class StrategyResult:
    """Result from strategy analysis"""
    signal: Signal
    confidence: float  # 0-100
    reasons: List[str]
    indicators: Dict[str, float]
    

class TradingStrategy:
    """
    Multi-indicator trading strategy combining:
    - RSI (Relative Strength Index)
    - MACD (Moving Average Convergence Divergence)
    - EMA Crossover (9/21)
    - Volume analysis
    """
    
    def __init__(self):
        self.rsi_period = config.RSI_PERIOD
        self.rsi_oversold = config.RSI_OVERSOLD
        self.rsi_overbought = config.RSI_OVERBOUGHT
        self.macd_fast = config.MACD_FAST
        self.macd_slow = config.MACD_SLOW
        self.macd_signal = config.MACD_SIGNAL
        self.ema_fast = config.EMA_FAST
        self.ema_slow = config.EMA_SLOW
        self.min_signals = config.MIN_SIGNALS_FOR_ENTRY
        
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate all technical indicators
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            DataFrame with added indicator columns
        """
        if df.empty or len(df) < 30:
            logger.warning("Insufficient data for indicator calculation")
            return df
            
        try:
            # RSI
            rsi = RSIIndicator(close=df['close'], window=self.rsi_period)
            df['rsi'] = rsi.rsi()
            
            # MACD
            macd = MACD(
                close=df['close'],
                window_fast=self.macd_fast,
                window_slow=self.macd_slow,
                window_sign=self.macd_signal
            )
            df['macd'] = macd.macd()
            df['macd_signal'] = macd.macd_signal()
            df['macd_histogram'] = macd.macd_diff()
            
            # EMA
            ema_fast = EMAIndicator(close=df['close'], window=self.ema_fast)
            ema_slow = EMAIndicator(close=df['close'], window=self.ema_slow)
            df['ema_fast'] = ema_fast.ema_indicator()
            df['ema_slow'] = ema_slow.ema_indicator()
            
            # Volume Moving Average
            df['volume_sma'] = df['volume'].rolling(window=20).mean()
            df['volume_ratio'] = df['volume'] / df['volume_sma']
            
            # Price change
            df['price_change'] = df['close'].pct_change()
            df['price_change_5'] = df['close'].pct_change(5)
            
            return df
            
        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")
            return df
    
    def analyze(self, df: pd.DataFrame) -> StrategyResult:
        """
        Analyze market data and generate trading signal
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            StrategyResult with signal and analysis
        """
        if df.empty or len(df) < 30:
            return StrategyResult(
                signal=Signal.HOLD,
                confidence=0,
                reasons=["Insufficient data"],
                indicators={}
            )
        
        # Calculate indicators if not present
        if 'rsi' not in df.columns:
            df = self.calculate_indicators(df)
        
        # Get latest values
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        
        buy_signals = []
        sell_signals = []
        reasons = []
        
        indicators = {
            'rsi': latest.get('rsi', 50),
            'macd': latest.get('macd', 0),
            'macd_signal': latest.get('macd_signal', 0),
            'macd_histogram': latest.get('macd_histogram', 0),
            'ema_fast': latest.get('ema_fast', 0),
            'ema_slow': latest.get('ema_slow', 0),
            'volume_ratio': latest.get('volume_ratio', 1),
            'price': latest['close']
        }
        
        # === RSI Analysis ===
        rsi = indicators['rsi']
        if not pd.isna(rsi):
            if rsi < self.rsi_oversold:
                buy_signals.append(('rsi', 1.5))  # Strong signal when oversold
                reasons.append(f"RSI oversold ({rsi:.1f} < {self.rsi_oversold})")
            elif rsi < 40:
                buy_signals.append(('rsi', 0.7))
                reasons.append(f"RSI low ({rsi:.1f})")
            elif rsi > self.rsi_overbought:
                sell_signals.append(('rsi', 1.5))
                reasons.append(f"RSI overbought ({rsi:.1f} > {self.rsi_overbought})")
            elif rsi > 60:
                sell_signals.append(('rsi', 0.5))
                reasons.append(f"RSI elevated ({rsi:.1f})")
        
        # === MACD Analysis ===
        macd = indicators['macd']
        macd_signal = indicators['macd_signal']
        macd_hist = indicators['macd_histogram']
        prev_macd_hist = prev.get('macd_histogram', 0)
        
        if not pd.isna(macd) and not pd.isna(macd_signal):
            # MACD crossover
            if macd > macd_signal and prev.get('macd', 0) <= prev.get('macd_signal', 0):
                buy_signals.append(('macd_crossover', 1.2))
                reasons.append("MACD bullish crossover")
            elif macd < macd_signal and prev.get('macd', 0) >= prev.get('macd_signal', 0):
                sell_signals.append(('macd_crossover', 1.2))
                reasons.append("MACD bearish crossover")
            
            # MACD histogram momentum
            if not pd.isna(macd_hist) and not pd.isna(prev_macd_hist):
                if macd_hist > 0 and macd_hist > prev_macd_hist:
                    buy_signals.append(('macd_momentum', 0.5))
                    reasons.append("MACD histogram increasing")
                elif macd_hist < 0 and macd_hist < prev_macd_hist:
                    sell_signals.append(('macd_momentum', 0.5))
                    reasons.append("MACD histogram decreasing")
        
        # === EMA Crossover Analysis ===
        ema_fast = indicators['ema_fast']
        ema_slow = indicators['ema_slow']
        prev_ema_fast = prev.get('ema_fast', 0)
        prev_ema_slow = prev.get('ema_slow', 0)
        
        if not pd.isna(ema_fast) and not pd.isna(ema_slow):
            # EMA crossover
            if ema_fast > ema_slow and prev_ema_fast <= prev_ema_slow:
                buy_signals.append(('ema_crossover', 1.3))
                reasons.append(f"EMA {self.ema_fast}/{self.ema_slow} bullish crossover")
            elif ema_fast < ema_slow and prev_ema_fast >= prev_ema_slow:
                sell_signals.append(('ema_crossover', 1.3))
                reasons.append(f"EMA {self.ema_fast}/{self.ema_slow} bearish crossover")
            
            # Trend direction
            if ema_fast > ema_slow:
                buy_signals.append(('trend', 0.3))
            else:
                sell_signals.append(('trend', 0.3))
        
        # === Volume Confirmation ===
        volume_ratio = indicators['volume_ratio']
        if not pd.isna(volume_ratio) and volume_ratio > 1.5:
            # High volume confirms signal
            if buy_signals:
                buy_signals.append(('volume', 0.5))
                reasons.append(f"High volume confirmation ({volume_ratio:.1f}x)")
            elif sell_signals:
                sell_signals.append(('volume', 0.5))
                reasons.append(f"High volume confirmation ({volume_ratio:.1f}x)")
        
        # === Calculate Final Signal ===
        buy_score = sum(s[1] for s in buy_signals)
        sell_score = sum(s[1] for s in sell_signals)
        
        # Determine signal and confidence
        if buy_score >= self.min_signals and buy_score > sell_score:
            if buy_score >= 3:
                signal = Signal.STRONG_BUY
            else:
                signal = Signal.BUY
            confidence = min(100, (buy_score / 4) * 100)
        elif sell_score >= self.min_signals and sell_score > buy_score:
            if sell_score >= 3:
                signal = Signal.STRONG_SELL
            else:
                signal = Signal.SELL
            confidence = min(100, (sell_score / 4) * 100)
        else:
            signal = Signal.HOLD
            confidence = 50
            if not reasons:
                reasons.append("No clear signals")
        
        return StrategyResult(
            signal=signal,
            confidence=confidence,
            reasons=reasons,
            indicators=indicators
        )
    
    def should_buy(self, df: pd.DataFrame) -> Tuple[bool, float, List[str]]:
        """
        Determine if conditions are right to buy
        
        Returns:
            (should_buy, confidence, reasons)
        """
        result = self.analyze(df)
        should = result.signal in [Signal.BUY, Signal.STRONG_BUY]
        return should, result.confidence, result.reasons
    
    def should_sell(self, df: pd.DataFrame, entry_price: float = None, 
                    current_price: float = None) -> Tuple[bool, float, List[str]]:
        """
        Determine if conditions are right to sell
        
        Returns:
            (should_sell, confidence, reasons)
        """
        result = self.analyze(df)
        reasons = result.reasons.copy()
        
        # Check technical sell signal
        tech_sell = result.signal in [Signal.SELL, Signal.STRONG_SELL]
        
        # Check stop-loss and take-profit if entry_price provided
        if entry_price and current_price:
            pnl_percent = ((current_price - entry_price) / entry_price) * 100
            
            # Stop-loss check
            if pnl_percent <= -config.STOP_LOSS_PERCENT:
                reasons.append(f"STOP-LOSS triggered ({pnl_percent:.2f}%)")
                return True, 100, reasons
            
            # Take-profit check
            if pnl_percent >= config.TAKE_PROFIT_PERCENT:
                reasons.append(f"TAKE-PROFIT triggered ({pnl_percent:.2f}%)")
                return True, 100, reasons
        
        return tech_sell, result.confidence, reasons


# Convenience function for quick analysis
def analyze_market(df: pd.DataFrame) -> StrategyResult:
    """Quick market analysis using default strategy"""
    strategy = TradingStrategy()
    return strategy.analyze(df)
