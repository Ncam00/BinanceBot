"""
Trading Execution Module
Orchestrates the trading loop
"""
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional
import json

import config
from bot.exchange import BinanceExchange
from bot.strategy import TradingStrategy, Signal
from bot.risk_manager import RiskManager
from bot.database import Database

logger = logging.getLogger(__name__)


class Trader:
    """Main trading orchestrator"""
    
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.running = False
        
        # Initialize components
        logger.info(f"Initializing trader in {'PAPER' if paper_mode else 'LIVE'} mode")
        
        self.exchange = BinanceExchange(paper_mode=paper_mode)
        self.strategy = TradingStrategy()
        self.db = Database()
        
        # Get starting capital
        self.starting_capital = self.exchange.get_total_portfolio_value()
        if self.starting_capital <= 0:
            self.starting_capital = config.STARTING_CAPITAL
            
        self.risk_manager = RiskManager(self.starting_capital)
        
        # Trading pairs to monitor
        self.trading_pairs = config.TRADING_PAIRS
        
        logger.info(f"Starting capital: ${self.starting_capital:.2f}")
        logger.info(f"Trading pairs: {self.trading_pairs}")
    
    def analyze_pair(self, symbol: str) -> Optional[Dict]:
        """Analyze a single trading pair"""
        try:
            # Get OHLCV data
            df = self.exchange.get_ohlcv(symbol, config.TIMEFRAME, limit=100)
            if df.empty:
                return None
            
            # Run strategy
            result = self.strategy.analyze(df)
            current_price = self.exchange.get_current_price(symbol)
            
            return {
                'symbol': symbol,
                'price': current_price,
                'signal': result.signal,
                'confidence': result.confidence,
                'reasons': result.reasons,
                'indicators': result.indicators
            }
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None
    
    def execute_buy(self, symbol: str, amount_usdt: float, analysis: Dict) -> bool:
        """Execute a buy order"""
        try:
            order = self.exchange.place_market_buy(symbol, amount_usdt)
            if not order:
                return False
            
            # Record in risk manager
            self.risk_manager.open_position(
                symbol=symbol,
                entry_price=order['price'],
                amount=order['amount']
            )
            
            # Record in database
            self.db.record_trade_entry(
                symbol=symbol,
                side='buy',
                entry_price=order['price'],
                amount=order['amount'],
                cost_usdt=amount_usdt,
                strategy='rsi_macd_ema',
                paper_trade=self.paper_mode
            )
            
            # Log signal
            self.db.record_signal(
                symbol=symbol,
                signal_type=analysis['signal'].name,
                confidence=analysis['confidence'],
                indicators=json.dumps(analysis['indicators']),
                reasons=', '.join(analysis['reasons']),
                acted_on=True
            )
            
            logger.info(f"{'[PAPER]' if self.paper_mode else '[LIVE]'} BUY {order['amount']:.6f} {symbol} @ ${order['price']:.4f}")
            return True
            
        except Exception as e:
            logger.error(f"Buy execution failed for {symbol}: {e}")
            return False
    
    def execute_sell(self, symbol: str, reason: str) -> bool:
        """Execute a sell order"""
        try:
            position = self.risk_manager.get_position(symbol)
            if not position:
                logger.warning(f"No position found for {symbol}")
                return False
            
            order = self.exchange.place_market_sell(symbol)
            if not order:
                return False
            
            # Close in risk manager
            pnl = self.risk_manager.close_position(symbol, order['price'])
            
            # Record in database
            self.db.record_trade_exit(symbol, order['price'], reason)
            
            logger.info(f"{'[PAPER]' if self.paper_mode else '[LIVE]'} SELL {order['amount']:.6f} {symbol} @ ${order['price']:.4f} | PnL: ${pnl:.2f} | Reason: {reason}")
            return True
            
        except Exception as e:
            logger.error(f"Sell execution failed for {symbol}: {e}")
            return False
    
    def check_exit_conditions(self) -> List[str]:
        """Check all positions for exit conditions"""
        exits = []
        
        for symbol in list(self.risk_manager.positions.keys()):
            position = self.risk_manager.get_position(symbol)
            if not position:
                continue
            
            current_price = self.exchange.get_current_price(symbol)
            if current_price <= 0:
                continue
            
            # Check stop-loss, take-profit, trailing stop
            should_exit, reason = position.should_exit(current_price)
            if should_exit:
                if self.execute_sell(symbol, reason):
                    exits.append(symbol)
                continue
            
            # Check technical sell signal
            df = self.exchange.get_ohlcv(symbol, config.TIMEFRAME, limit=100)
            should_sell, confidence, reasons = self.strategy.should_sell(
                df, position.entry_price, current_price
            )
            
            if should_sell and confidence >= 70:
                if self.execute_sell(symbol, f"Technical signal: {', '.join(reasons[:2])}"):
                    exits.append(symbol)
        
        return exits
    
    def find_entry_opportunities(self) -> List[Dict]:
        """Find buy opportunities across all pairs"""
        opportunities = []
        
        for symbol in self.trading_pairs:
            # Skip if already have position
            if self.risk_manager.has_position(symbol):
                continue
            
            analysis = self.analyze_pair(symbol)
            if not analysis:
                continue
            
            # Log signal (even if not acted on)
            if analysis['signal'] != Signal.HOLD:
                self.db.record_signal(
                    symbol=symbol,
                    signal_type=analysis['signal'].name,
                    confidence=analysis['confidence'],
                    indicators=json.dumps(analysis['indicators']),
                    reasons=', '.join(analysis['reasons']),
                    acted_on=False
                )
            
            # Check for buy signal
            if analysis['signal'] in [Signal.BUY, Signal.STRONG_BUY]:
                if analysis['confidence'] >= 60:  # Minimum confidence threshold
                    opportunities.append(analysis)
        
        # Sort by confidence
        opportunities.sort(key=lambda x: x['confidence'], reverse=True)
        return opportunities
    
    def trading_loop_iteration(self) -> Dict:
        """Single iteration of the trading loop"""
        result = {
            'timestamp': datetime.now().isoformat(),
            'positions_checked': 0,
            'exits': [],
            'entries': [],
            'errors': []
        }
        
        try:
            current_balance = self.exchange.get_total_portfolio_value()
            
            # Check if trading is allowed
            can_trade, block_reason = self.risk_manager.can_trade(current_balance)
            if not can_trade:
                logger.warning(f"Trading blocked: {block_reason}")
                result['block_reason'] = block_reason
                return result
            
            # 1. Check exit conditions for open positions
            result['positions_checked'] = len(self.risk_manager.positions)
            exits = self.check_exit_conditions()
            result['exits'] = exits
            
            # 2. Look for new entry opportunities
            opportunities = self.find_entry_opportunities()
            
            for opp in opportunities:
                # Calculate position size
                position_size = self.risk_manager.calculate_position_size(
                    opp['symbol'], current_balance
                )
                
                if position_size <= 0:
                    logger.info(f"Position size too small for {opp['symbol']}, skipping")
                    continue
                
                # Execute buy
                if self.execute_buy(opp['symbol'], position_size, opp):
                    result['entries'].append({
                        'symbol': opp['symbol'],
                        'size': position_size,
                        'confidence': opp['confidence']
                    })
                    
                    # Refresh balance after trade
                    current_balance = self.exchange.get_total_portfolio_value()
            
            # Record balance snapshot periodically
            self.db.record_balance(
                current_balance,
                len(self.risk_manager.positions),
                sum(p.unrealized_pnl(self.exchange.get_current_price(p.symbol)) 
                    for p in self.risk_manager.positions.values())
            )
            
        except Exception as e:
            logger.error(f"Error in trading loop: {e}")
            result['errors'].append(str(e))
        
        return result
    
    def run(self, iterations: int = None):
        """
        Run the trading bot
        
        Args:
            iterations: Number of iterations (None = run forever)
        """
        self.running = True
        iteration = 0
        
        logger.info("=" * 60)
        logger.info(f"TRADING BOT STARTED - {'PAPER' if self.paper_mode else 'LIVE'} MODE")
        logger.info(f"Capital: ${self.starting_capital:.2f} | Pairs: {len(self.trading_pairs)}")
        logger.info("=" * 60)
        
        try:
            while self.running:
                iteration += 1
                
                if iterations and iteration > iterations:
                    break
                
                logger.info(f"\n--- Iteration {iteration} @ {datetime.now().strftime('%H:%M:%S')} ---")
                
                result = self.trading_loop_iteration()
                
                # Log summary
                if result.get('exits'):
                    logger.info(f"Exits: {result['exits']}")
                if result.get('entries'):
                    logger.info(f"Entries: {[e['symbol'] for e in result['entries']]}")
                if result.get('block_reason'):
                    logger.warning(f"Blocked: {result['block_reason']}")
                
                # Show status
                stats = self.risk_manager.get_stats(self.exchange.get_total_portfolio_value())
                logger.info(f"Balance: ${stats['current_balance']:.2f} | "
                           f"Daily P&L: ${stats['daily_pnl']:.2f} ({stats['daily_pnl_percent']:.2f}%) | "
                           f"Positions: {stats['open_positions']}")
                
                # Wait for next interval
                if self.running and (iterations is None or iteration < iterations):
                    time.sleep(config.LOOP_INTERVAL)
                    
        except KeyboardInterrupt:
            logger.info("\nBot stopped by user")
        finally:
            self.running = False
            logger.info("Trading bot stopped")
    
    def stop(self):
        """Stop the trading bot"""
        self.running = False
    
    def get_status(self) -> Dict:
        """Get current bot status"""
        current_balance = self.exchange.get_total_portfolio_value()
        stats = self.risk_manager.get_stats(current_balance)
        db_stats = self.db.get_stats(days=7)
        
        positions_info = []
        for symbol, pos in self.risk_manager.positions.items():
            current_price = self.exchange.get_current_price(symbol)
            positions_info.append({
                'symbol': symbol,
                'entry_price': pos.entry_price,
                'current_price': current_price,
                'amount': pos.amount,
                'pnl': pos.unrealized_pnl(current_price),
                'pnl_percent': pos.unrealized_pnl_percent(current_price)
            })
        
        return {
            'running': self.running,
            'mode': 'paper' if self.paper_mode else 'live',
            'balance': current_balance,
            'starting_capital': self.starting_capital,
            'total_return': stats['total_return_percent'],
            'daily_pnl': stats['daily_pnl'],
            'daily_pnl_percent': stats['daily_pnl_percent'],
            'positions': positions_info,
            'stats_7d': db_stats
        }
