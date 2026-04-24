import os
from dotenv import load_dotenv

load_dotenv()

# ==============================
# API
# ==============================
API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_SECRET_KEY", "")

# ==============================
# TELEGRAM
# ==============================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ==============================
# TRADING
# ==============================
TRADING_PAIRS = ["BTCUSDT", "ETHUSDT"]
POSITION_SIZE_PCT = 0.10
DAILY_TRADE_LIMIT = 3

# ==============================
# STRATEGY
# ==============================
ATR_SL_MULTIPLIER = 1.5
ATR_TP_MULTIPLIER = 2.0
TRAILING_STOP = 0.992
TIME_EXIT_CANDLES = 10
