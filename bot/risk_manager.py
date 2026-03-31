"""
Risk Management Module
Implements position sizing, stop-loss, circuit breakers
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass, field
import json
from pathlib import Path

import config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open trading position"""
    symbol: str
    entry_price: float
    amount: float
    entry_time: datetime
    stop_loss: float
    take_profit: float
    trailing_stop: Optional[float] = None
    highest_price: float = 0.0
    
    def __post_init__(self):
        if self.highest_price == 0:
            self.highest_price = self.entry_price
    
    @property
    def cost_basis(self) -> float:
        return self.entry_price * self.amount
    
    def current_value(self, price: float) -> float:
        return price * self.amount
    
    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.amount
    
    def unrealized_pnl_percent(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0
        return ((current_price - self.entry_price) / self.entry_price) * 100
    
    def update_trailing_stop(self, current_price: float) -> bool:
        """Update trailing stop if price moved up. Returns True if stop hit."""
        if current_price > self.highest_price:
            self.highest_price = current_price
            
            # Activate trailing stop after threshold
            profit_percent = self.unrealized_pnl_percent(current_price)
            if profit_percent >= config.TRAILING_STOP_ACTIVATION:
                new_stop = current_price * (1 - config.TRAILING_STOP_DISTANCE / 100)
                if self.trailing_stop is None or new_stop > self.trailing_stop:
                    self.trailing_stop = new_stop
                    logger.info(f"{self.symbol}: Trailing stop updated to {new_stop:.4f}")
        
        # Check if trailing stop hit
        if self.trailing_stop and current_price <= self.trailing_stop:
            return True
        return False
    
    def should_exit(self, current_price: float) -> Tuple[bool, str]:
        """Check if position should be exited"""
        # Check trailing stop first (if active)
        if self.update_trailing_stop(current_price):
            return True, f"Trailing stop hit at {self.trailing_stop:.4f}"
        
        # Check hard stop-loss
        if current_price <= self.stop_loss:
            return True, f"Stop-loss hit at {current_price:.4f}"
        
        # Check take-profit
        if current_price >= self.take_profit:
            return True, f"Take-profit hit at {current_price:.4f}"
        
        return False, ""
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'entry_price': self.entry_price,
            'amount': self.amount,
            'entry_time': self.entry_time.isoformat(),
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'trailing_stop': self.trailing_stop,
            'highest_price': self.highest_price
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Position':
        return cls(
            symbol=data['symbol'],
            entry_price=data['entry_price'],
            amount=data['amount'],
            entry_time=datetime.fromisoformat(data['entry_time']),
            stop_loss=data['stop_loss'],
            take_profit=data['take_profit'],
            trailing_stop=data.get('trailing_stop'),
            highest_price=data.get('highest_price', data['entry_price'])
        )


@dataclass  
class TradingState:
    """Tracks overall trading state for circuit breakers"""
    daily_start_balance: float = 0.0
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    last_trade_time: Optional[datetime] = None
    pause_until: Optional[datetime] = None
    max_balance: float = 0.0
    trades_today: int = 0
    last_reset_date: Optional[str] = None
    
    def reset_daily(self, current_balance: float):
        """Reset daily tracking"""
        today = datetime.now().strftime('%Y-%m-%d')
        if self.last_reset_date != today:
            self.daily_start_balance = current_balance
            self.daily_pnl = 0.0
            self.trades_today = 0
            self.last_reset_date = today
            logger.info(f"Daily stats reset. Starting balance: ${current_balance:.2f}")
    
    def record_trade(self, pnl: float):
        """Record a completed trade"""
        self.daily_pnl += pnl
        self.trades_today += 1
        self.last_trade_time = datetime.now()
        
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
    
    def to_dict(self) -> dict:
        return {
            'daily_start_balance': self.daily_start_balance,
            'daily_pnl': self.daily_pnl,
            'consecutive_losses': self.consecutive_losses,
            'last_trade_time': self.last_trade_time.isoformat() if self.last_trade_time else None,
            'pause_until': self.pause_until.isoformat() if self.pause_until else None,
            'max_balance': self.max_balance,
            'trades_today': self.trades_today,
            'last_reset_date': self.last_reset_date
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'TradingState':
        state = cls()
        state.daily_start_balance = data.get('daily_start_balance', 0)
        state.daily_pnl = data.get('daily_pnl', 0)
        state.consecutive_losses = data.get('consecutive_losses', 0)
        state.last_trade_time = datetime.fromisoformat(data['last_trade_time']) if data.get('last_trade_time') else None
        state.pause_until = datetime.fromisoformat(data['pause_until']) if data.get('pause_until') else None
        state.max_balance = data.get('max_balance', 0)
        state.trades_today = data.get('trades_today', 0)
        state.last_reset_date = data.get('last_reset_date')
        return state


class RiskManager:
    """Manages trading risk, position sizing, and circuit breakers"""
    
    def __init__(self, starting_capital: float):
        self.starting_capital = starting_capital
        self.positions: Dict[str, Position] = {}
        self.state = TradingState()
        self.state.max_balance = starting_capital
        self.state_file = config.DATABASE_PATH.parent / "risk_state.json"
        
        # Ensure data directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing state if available
        self._load_state()
    
    def _load_state(self):
        """Load state from file"""
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                self.state = TradingState.from_dict(data.get('state', {}))
                for pos_data in data.get('positions', []):
                    pos = Position.from_dict(pos_data)
                    self.positions[pos.symbol] = pos
                logger.info(f"Loaded risk state: {len(self.positions)} positions")
        except Exception as e:
            logger.warning(f"Could not load risk state: {e}")
    
    def _save_state(self):
        """Save state to file"""
        try:
            data = {
                'state': self.state.to_dict(),
                'positions': [p.to_dict() for p in self.positions.values()]
            }
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save risk state: {e}")
    
    def calculate_position_size(self, symbol: str, current_balance: float) -> float:
        """
        Calculate appropriate position size for a trade
        
        Args:
            symbol: Trading pair
            current_balance: Current portfolio value in USDT
            
        Returns:
            Position size in USDT
        """
        # Base position size
        max_size = current_balance * (config.MAX_POSITION_SIZE_PERCENT / 100)
        
        # Reduce size if already have positions
        num_positions = len(self.positions)
        if num_positions >= config.MAX_CONCURRENT_POSITIONS:
            return 0  # Cannot open more positions
        
        # Reduce size proportionally based on existing positions
        available_slots = config.MAX_CONCURRENT_POSITIONS - num_positions
        adjusted_size = max_size * (available_slots / config.MAX_CONCURRENT_POSITIONS)
        
        # Further reduce after consecutive losses
        if self.state.consecutive_losses > 0:
            loss_factor = max(0.5, 1 - (self.state.consecutive_losses * 0.15))
            adjusted_size *= loss_factor
            logger.info(f"Position reduced by {(1-loss_factor)*100:.0f}% due to {self.state.consecutive_losses} losses")
        
        # Minimum trade size (Binance typically requires ~$10 minimum)
        min_size = 12.0
        if adjusted_size < min_size:
            logger.warning(f"Position size ${adjusted_size:.2f} below minimum ${min_size}")
            return 0
        
        return adjusted_size
    
    def can_trade(self, current_balance: float) -> Tuple[bool, str]:
        """
        Check if trading is allowed based on circuit breakers
        
        Returns:
            (can_trade, reason_if_not)
        """
        now = datetime.now()
        
        # Reset daily stats if needed
        self.state.reset_daily(current_balance)
        
        # Check pause timer
        if self.state.pause_until and now < self.state.pause_until:
            remaining = (self.state.pause_until - now).seconds // 60
            return False, f"Trading paused for {remaining} more minutes"
        
        # Check daily loss limit
        daily_loss_percent = (self.state.daily_pnl / self.state.daily_start_balance * 100) if self.state.daily_start_balance > 0 else 0
        if daily_loss_percent <= -config.DAILY_LOSS_LIMIT_PERCENT:
            self.state.pause_until = now + timedelta(hours=24)
            self._save_state()
            return False, f"Daily loss limit hit ({daily_loss_percent:.1f}%). Paused 24h."
        
        # Check consecutive losses
        if self.state.consecutive_losses >= config.CONSECUTIVE_LOSS_PAUSE:
            self.state.pause_until = now + timedelta(hours=config.CONSECUTIVE_LOSS_PAUSE_HOURS)
            self._save_state()
            return False, f"{self.state.consecutive_losses} consecutive losses. Paused {config.CONSECUTIVE_LOSS_PAUSE_HOURS}h."
        
        # Check max drawdown
        if self.state.max_balance > 0:
            drawdown_percent = ((self.state.max_balance - current_balance) / self.state.max_balance) * 100
            if drawdown_percent >= config.MAX_DRAWDOWN_PERCENT:
                return False, f"Max drawdown hit ({drawdown_percent:.1f}%). MANUAL RESTART REQUIRED."
        
        # Update max balance
        if current_balance > self.state.max_balance:
            self.state.max_balance = current_balance
        
        return True, ""
    
    def open_position(self, symbol: str, entry_price: float, amount: float) -> Position:
        """Create and track a new position"""
        stop_loss = entry_price * (1 - config.STOP_LOSS_PERCENT / 100)
        take_profit = entry_price * (1 + config.TAKE_PROFIT_PERCENT / 100)
        
        position = Position(
            symbol=symbol,
            entry_price=entry_price,
            amount=amount,
            entry_time=datetime.now(),
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        
        self.positions[symbol] = position
        self._save_state()
        
        logger.info(f"Opened position: {symbol} @ {entry_price:.4f} | SL: {stop_loss:.4f} | TP: {take_profit:.4f}")
        return position
    
    def close_position(self, symbol: str, exit_price: float) -> Optional[float]:
        """Close a position and record PnL"""
        if symbol not in self.positions:
            return None
        
        position = self.positions[symbol]
        pnl = position.unrealized_pnl(exit_price)
        pnl_percent = position.unrealized_pnl_percent(exit_price)
        
        self.state.record_trade(pnl)
        
        del self.positions[symbol]
        self._save_state()
        
        logger.info(f"Closed position: {symbol} @ {exit_price:.4f} | PnL: ${pnl:.2f} ({pnl_percent:.2f}%)")
        return pnl
    
    def check_positions(self, prices: Dict[str, float]) -> List[Tuple[str, str]]:
        """
        Check all positions for exit conditions
        
        Args:
            prices: Dict of symbol -> current price
            
        Returns:
            List of (symbol, reason) for positions that should exit
        """
        exits = []
        for symbol, position in self.positions.items():
            if symbol in prices:
                should_exit, reason = position.should_exit(prices[symbol])
                if should_exit:
                    exits.append((symbol, reason))
        return exits
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a symbol"""
        return self.positions.get(symbol)
    
    def has_position(self, symbol: str) -> bool:
        """Check if we have an open position"""
        return symbol in self.positions
    
    def get_stats(self, current_balance: float) -> Dict:
        """Get current risk/trading statistics"""
        return {
            'current_balance': current_balance,
            'daily_pnl': self.state.daily_pnl,
            'daily_pnl_percent': (self.state.daily_pnl / self.state.daily_start_balance * 100) if self.state.daily_start_balance > 0 else 0,
            'trades_today': self.state.trades_today,
            'consecutive_losses': self.state.consecutive_losses,
            'open_positions': len(self.positions),
            'max_balance': self.state.max_balance,
            'total_return_percent': ((current_balance - self.starting_capital) / self.starting_capital * 100) if self.starting_capital > 0 else 0
        }
    
    def force_unpause(self):
        """Manually unpause trading"""
        self.state.pause_until = None
        self.state.consecutive_losses = 0
        self._save_state()
        logger.info("Trading manually unpaused")
