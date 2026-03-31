"""
Configuration settings for the Binance Trading Bot
"""
import os
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
load_dotenv()

# ============ API CONFIGURATION ============
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")

# ============ TRADING MODE ============
# "paper" = simulation mode (no real trades)
# "live" = real money trading
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

# ============ TRADING PAIRS ============
# Pairs to trade (in order of priority)
DEFAULT_PAIRS = [
    "BTC/USDT",   # Bitcoin - most stable
    "ETH/USDT",   # Ethereum - second most stable  
    "SOL/USDT",   # Solana - higher volatility
]

TRADING_PAIRS = os.getenv("TRADING_PAIRS", ",".join(DEFAULT_PAIRS)).split(",")
TRADING_PAIRS = [p.strip() for p in TRADING_PAIRS if p.strip()]

# ============ POSITION SIZING ============
# Starting capital (will be fetched from exchange if live)
STARTING_CAPITAL = 300.0  # NZD worth in USDT

# Maximum % of portfolio per trade
MAX_POSITION_SIZE_PERCENT = float(os.getenv("MAX_POSITION_SIZE_PERCENT", "10"))

# Maximum concurrent positions
MAX_CONCURRENT_POSITIONS = 3

# ============ RISK MANAGEMENT ============
# Stop-loss percentage (triggers sell if price drops this much)
STOP_LOSS_PERCENT = 3.0

# Take-profit percentage (triggers sell if price rises this much)
TAKE_PROFIT_PERCENT = 5.0

# Trailing stop activation (start trailing after this profit)
TRAILING_STOP_ACTIVATION = 3.0

# Trailing stop distance
TRAILING_STOP_DISTANCE = 1.5

# Daily loss limit - pause trading if daily loss exceeds this
DAILY_LOSS_LIMIT_PERCENT = float(os.getenv("DAILY_LOSS_LIMIT_PERCENT", "5"))

# Maximum drawdown before requiring manual restart
MAX_DRAWDOWN_PERCENT = float(os.getenv("MAX_DRAWDOWN_PERCENT", "15"))

# Consecutive losses before pause
CONSECUTIVE_LOSS_PAUSE = 3
CONSECUTIVE_LOSS_PAUSE_HOURS = 4

# ============ STRATEGY SETTINGS ============
# Timeframe for analysis
TIMEFRAME = "15m"  # 15-minute candles

# RSI settings
RSI_PERIOD = 14
RSI_OVERSOLD = 30  # Buy signal
RSI_OVERBOUGHT = 70  # Sell signal

# MACD settings
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# EMA settings
EMA_FAST = 9
EMA_SLOW = 21

# Minimum signals required to enter trade
MIN_SIGNALS_FOR_ENTRY = 2

# ============ EXECUTION SETTINGS ============
# Main loop interval (seconds)
LOOP_INTERVAL = 60  # Check every minute

# Order timeout (seconds)
ORDER_TIMEOUT = 30

# Retry attempts for failed orders
ORDER_RETRY_ATTEMPTS = 3

# ============ LOGGING ============
LOG_LEVEL = "INFO"
LOG_FILE = "trading_bot.log"

# ============ DATABASE ============
DATABASE_PATH = Path(__file__).parent / "data" / "trades.db"

# ============ DISPLAY ============
# Refresh rate for dashboard (seconds)
DASHBOARD_REFRESH = 5
