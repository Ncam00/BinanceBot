"""
Database Module
SQLite storage for trade history and analytics
"""
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path
from contextlib import contextmanager

import config

logger = logging.getLogger(__name__)


class Database:
    """SQLite database for trade history and bot state"""
    
    def __init__(self, db_path: Path = None):
        self.db_path = db_path or config.DATABASE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize database tables"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Trades table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL,
                    exit_price REAL,
                    amount REAL NOT NULL,
                    cost_usdt REAL,
                    pnl_usdt REAL,
                    pnl_percent REAL,
                    entry_time TIMESTAMP,
                    exit_time TIMESTAMP,
                    exit_reason TEXT,
                    strategy TEXT,
                    paper_trade INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Signals table (for logging analysis)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    confidence REAL,
                    indicators TEXT,
                    reasons TEXT,
                    acted_on INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Balance history
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS balance_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    balance_usdt REAL NOT NULL,
                    open_positions INTEGER,
                    unrealized_pnl REAL,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol)')
            
            logger.info(f"Database initialized at {self.db_path}")
    
    def record_trade_entry(self, symbol: str, side: str, entry_price: float, 
                          amount: float, cost_usdt: float, strategy: str = "default",
                          paper_trade: bool = True) -> int:
        """Record a new trade entry"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trades (symbol, side, entry_price, amount, cost_usdt, 
                                   entry_time, strategy, paper_trade)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (symbol, side, entry_price, amount, cost_usdt, 
                 datetime.now().isoformat(), strategy, 1 if paper_trade else 0))
            
            trade_id = cursor.lastrowid
            logger.info(f"Recorded trade entry #{trade_id}: {side} {amount} {symbol} @ {entry_price}")
            return trade_id
    
    def record_trade_exit(self, symbol: str, exit_price: float, exit_reason: str) -> Optional[Dict]:
        """Record trade exit for the most recent open trade of this symbol"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Find the open trade
            cursor.execute('''
                SELECT id, entry_price, amount, cost_usdt, entry_time 
                FROM trades 
                WHERE symbol = ? AND exit_time IS NULL 
                ORDER BY entry_time DESC LIMIT 1
            ''', (symbol,))
            
            row = cursor.fetchone()
            if not row:
                logger.warning(f"No open trade found for {symbol}")
                return None
            
            trade_id = row['id']
            entry_price = row['entry_price']
            amount = row['amount']
            cost_usdt = row['cost_usdt']
            
            # Calculate PnL
            proceeds = exit_price * amount
            pnl_usdt = proceeds - cost_usdt
            pnl_percent = ((exit_price - entry_price) / entry_price) * 100
            
            # Update trade
            cursor.execute('''
                UPDATE trades 
                SET exit_price = ?, exit_time = ?, exit_reason = ?, 
                    pnl_usdt = ?, pnl_percent = ?
                WHERE id = ?
            ''', (exit_price, datetime.now().isoformat(), exit_reason, 
                 pnl_usdt, pnl_percent, trade_id))
            
            result = {
                'trade_id': trade_id,
                'symbol': symbol,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'amount': amount,
                'pnl_usdt': pnl_usdt,
                'pnl_percent': pnl_percent,
                'exit_reason': exit_reason
            }
            
            logger.info(f"Trade #{trade_id} closed: PnL ${pnl_usdt:.2f} ({pnl_percent:.2f}%)")
            return result
    
    def record_signal(self, symbol: str, signal_type: str, confidence: float,
                     indicators: str, reasons: str, acted_on: bool = False):
        """Record a trading signal for analysis"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO signals (symbol, signal_type, confidence, indicators, reasons, acted_on)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (symbol, signal_type, confidence, indicators, reasons, 1 if acted_on else 0))
    
    def record_balance(self, balance_usdt: float, open_positions: int = 0, 
                       unrealized_pnl: float = 0):
        """Record balance snapshot"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO balance_history (balance_usdt, open_positions, unrealized_pnl)
                VALUES (?, ?, ?)
            ''', (balance_usdt, open_positions, unrealized_pnl))
    
    def get_trades(self, limit: int = 50, symbol: str = None, 
                   days: int = None) -> List[Dict]:
        """Get trade history"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM trades WHERE exit_time IS NOT NULL"
            params = []
            
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            
            if days:
                cutoff = (datetime.now() - timedelta(days=days)).isoformat()
                query += " AND entry_time > ?"
                params.append(cutoff)
            
            query += " ORDER BY exit_time DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
    
    def get_open_trades(self) -> List[Dict]:
        """Get all open trades"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM trades WHERE exit_time IS NULL
                ORDER BY entry_time DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]
    
    def get_stats(self, days: int = 30) -> Dict:
        """Get trading statistics"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) as winning_trades,
                    SUM(CASE WHEN pnl_usdt < 0 THEN 1 ELSE 0 END) as losing_trades,
                    SUM(pnl_usdt) as total_pnl,
                    AVG(pnl_usdt) as avg_pnl,
                    AVG(pnl_percent) as avg_pnl_percent,
                    MAX(pnl_usdt) as best_trade,
                    MIN(pnl_usdt) as worst_trade
                FROM trades 
                WHERE exit_time IS NOT NULL AND entry_time > ?
            ''', (cutoff,))
            
            row = cursor.fetchone()
            
            total = row['total_trades'] or 0
            wins = row['winning_trades'] or 0
            
            return {
                'total_trades': total,
                'winning_trades': wins,
                'losing_trades': row['losing_trades'] or 0,
                'win_rate': (wins / total * 100) if total > 0 else 0,
                'total_pnl': row['total_pnl'] or 0,
                'avg_pnl': row['avg_pnl'] or 0,
                'avg_pnl_percent': row['avg_pnl_percent'] or 0,
                'best_trade': row['best_trade'] or 0,
                'worst_trade': row['worst_trade'] or 0,
                'period_days': days
            }
    
    def get_balance_history(self, limit: int = 100) -> List[Dict]:
        """Get balance history"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM balance_history 
                ORDER BY recorded_at DESC LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cursor.fetchall()]
