"""
Binance Exchange API Wrapper
Handles all communication with Binance exchange
"""
import ccxt
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import pandas as pd
import config

logger = logging.getLogger(__name__)


class BinanceExchange:
    """Wrapper for Binance exchange operations"""
    
    def __init__(self, paper_mode: bool = True):
        """
        Initialize Binance exchange connection
        
        Args:
            paper_mode: If True, use sandbox/simulation mode
        """
        self.paper_mode = paper_mode
        self.exchange = None
        self.paper_balance = {"USDT": config.STARTING_CAPITAL}
        self.paper_positions = {}
        self._connect()
        
    def _connect(self):
        """Establish connection to Binance"""
        try:
            self.exchange = ccxt.binance({
                'apiKey': config.BINANCE_API_KEY,
                'secret': config.BINANCE_SECRET_KEY,
                'sandbox': self.paper_mode,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'spot',
                    'adjustForTimeDifference': True,
                }
            })
            
            if not self.paper_mode and config.BINANCE_API_KEY:
                # Test connection with live API
                self.exchange.load_markets()
                logger.info("Connected to Binance (LIVE MODE)")
            else:
                self.exchange.load_markets()
                logger.info("Connected to Binance (PAPER MODE)")
                
        except Exception as e:
            logger.error(f"Failed to connect to Binance: {e}")
            raise
    
    def get_balance(self, currency: str = "USDT") -> float:
        """
        Get available balance for a currency
        
        Args:
            currency: Currency symbol (e.g., "USDT", "BTC")
            
        Returns:
            Available balance
        """
        if self.paper_mode:
            return self.paper_balance.get(currency, 0.0)
            
        try:
            balance = self.exchange.fetch_balance()
            return float(balance.get(currency, {}).get('free', 0))
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return 0.0
    
    def get_total_portfolio_value(self) -> float:
        """Get total portfolio value in USDT"""
        if self.paper_mode:
            total = self.paper_balance.get("USDT", 0)
            for symbol, position in self.paper_positions.items():
                current_price = self.get_current_price(symbol)
                total += position['amount'] * current_price
            return total
            
        try:
            balance = self.exchange.fetch_balance()
            total = 0.0
            for currency, amounts in balance.get('total', {}).items():
                if amounts > 0:
                    if currency == 'USDT':
                        total += amounts
                    else:
                        try:
                            ticker = self.exchange.fetch_ticker(f"{currency}/USDT")
                            total += amounts * ticker['last']
                        except:
                            pass
            return total
        except Exception as e:
            logger.error(f"Error fetching portfolio value: {e}")
            return 0.0
    
    def get_current_price(self, symbol: str) -> float:
        """
        Get current price for a trading pair
        
        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            
        Returns:
            Current price
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return float(ticker['last'])
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return 0.0
    
    def get_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> pd.DataFrame:
        """
        Get OHLCV candle data
        
        Args:
            symbol: Trading pair
            timeframe: Candle timeframe (1m, 5m, 15m, 1h, etc.)
            limit: Number of candles to fetch
            
        Returns:
            DataFrame with OHLCV data
        """
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol}: {e}")
            return pd.DataFrame()
    
    def place_market_buy(self, symbol: str, amount_usdt: float) -> Optional[Dict]:
        """
        Place a market buy order
        
        Args:
            symbol: Trading pair
            amount_usdt: Amount in USDT to spend
            
        Returns:
            Order info dict or None if failed
        """
        try:
            current_price = self.get_current_price(symbol)
            if current_price <= 0:
                logger.error(f"Invalid price for {symbol}")
                return None
                
            # Calculate quantity
            quantity = amount_usdt / current_price
            
            # Get market precision
            market = self.exchange.market(symbol)
            quantity = self.exchange.amount_to_precision(symbol, quantity)
            quantity = float(quantity)
            
            if self.paper_mode:
                # Simulate order
                order = {
                    'id': f"paper_{datetime.now().timestamp()}",
                    'symbol': symbol,
                    'side': 'buy',
                    'type': 'market',
                    'price': current_price,
                    'amount': quantity,
                    'cost': amount_usdt,
                    'status': 'closed',
                    'timestamp': datetime.now().isoformat()
                }
                
                # Update paper balance
                self.paper_balance['USDT'] = self.paper_balance.get('USDT', 0) - amount_usdt
                base_currency = symbol.split('/')[0]
                self.paper_balance[base_currency] = self.paper_balance.get(base_currency, 0) + quantity
                
                # Track position
                self.paper_positions[symbol] = {
                    'amount': quantity,
                    'entry_price': current_price,
                    'entry_time': datetime.now()
                }
                
                logger.info(f"PAPER BUY: {quantity} {symbol} @ {current_price}")
                return order
            else:
                # Place real order
                order = self.exchange.create_market_buy_order(symbol, quantity)
                logger.info(f"LIVE BUY: {quantity} {symbol} @ {current_price}")
                return order
                
        except Exception as e:
            logger.error(f"Error placing buy order for {symbol}: {e}")
            return None
    
    def place_market_sell(self, symbol: str, quantity: float = None) -> Optional[Dict]:
        """
        Place a market sell order
        
        Args:
            symbol: Trading pair
            quantity: Amount to sell (if None, sells entire position)
            
        Returns:
            Order info dict or None if failed
        """
        try:
            current_price = self.get_current_price(symbol)
            if current_price <= 0:
                logger.error(f"Invalid price for {symbol}")
                return None
            
            base_currency = symbol.split('/')[0]
            
            if self.paper_mode:
                # Get paper position
                if quantity is None:
                    quantity = self.paper_balance.get(base_currency, 0)
                    
                if quantity <= 0:
                    logger.warning(f"No position to sell for {symbol}")
                    return None
                
                proceeds = quantity * current_price
                
                order = {
                    'id': f"paper_{datetime.now().timestamp()}",
                    'symbol': symbol,
                    'side': 'sell',
                    'type': 'market',
                    'price': current_price,
                    'amount': quantity,
                    'cost': proceeds,
                    'status': 'closed',
                    'timestamp': datetime.now().isoformat()
                }
                
                # Update paper balance
                self.paper_balance['USDT'] = self.paper_balance.get('USDT', 0) + proceeds
                self.paper_balance[base_currency] = self.paper_balance.get(base_currency, 0) - quantity
                
                # Remove position if fully sold
                if self.paper_balance[base_currency] <= 0:
                    self.paper_positions.pop(symbol, None)
                    self.paper_balance[base_currency] = 0
                
                logger.info(f"PAPER SELL: {quantity} {symbol} @ {current_price}")
                return order
            else:
                # Place real order
                if quantity is None:
                    balance = self.exchange.fetch_balance()
                    quantity = balance.get(base_currency, {}).get('free', 0)
                    
                if quantity <= 0:
                    logger.warning(f"No position to sell for {symbol}")
                    return None
                    
                quantity = self.exchange.amount_to_precision(symbol, quantity)
                quantity = float(quantity)
                
                order = self.exchange.create_market_sell_order(symbol, quantity)
                logger.info(f"LIVE SELL: {quantity} {symbol} @ {current_price}")
                return order
                
        except Exception as e:
            logger.error(f"Error placing sell order for {symbol}: {e}")
            return None
    
    def get_open_positions(self) -> Dict[str, Dict]:
        """Get all open positions"""
        if self.paper_mode:
            return self.paper_positions.copy()
            
        positions = {}
        try:
            balance = self.exchange.fetch_balance()
            for currency, amounts in balance.get('free', {}).items():
                if amounts > 0 and currency != 'USDT':
                    symbol = f"{currency}/USDT"
                    try:
                        current_price = self.get_current_price(symbol)
                        positions[symbol] = {
                            'amount': amounts,
                            'current_price': current_price,
                            'value': amounts * current_price
                        }
                    except:
                        pass
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            
        return positions
    
    def get_order_book(self, symbol: str, limit: int = 10) -> Dict:
        """Get order book for a symbol"""
        try:
            return self.exchange.fetch_order_book(symbol, limit)
        except Exception as e:
            logger.error(f"Error fetching order book for {symbol}: {e}")
            return {'bids': [], 'asks': []}
