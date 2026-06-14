import os
import json
from dotenv import load_dotenv

load_dotenv()


def get_daily_trade_limit():
    """
    Automatically scales daily trade limit based on proven performance:
      < 15 closed trades              → 3 trades/day (default)
      15+ trades AND win rate >= 40%  → 5 trades/day
      30+ trades AND win rate >= 40%  → 8 trades/day
    """
    log_path = os.path.join(os.path.dirname(__file__), "trade_log.jsonl")
    if not os.path.exists(log_path):
        return 3

    trades = []
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    t = json.loads(line)
                    if t.get("status") == "closed":
                        trades.append(t)
    except Exception:
        return 3

    n = len(trades)
    if n < 15:
        return 3

    win_rate = len([t for t in trades if t.get("pnl_usd", 0) > 0]) / n

    if n >= 30 and win_rate >= 0.40:
        return 8
    if n >= 15 and win_rate >= 0.40:
        return 5
    return 3

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
TRADING_PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
POSITION_SIZE_PCT = 0.10
DAILY_TRADE_LIMIT = get_daily_trade_limit()  # auto-scales: 3 → 5 → 8 based on win rate

# ==============================
# STRATEGY
# ==============================
ATR_SL_MULTIPLIER = 1.5
ATR_TP_MULTIPLIER = 2.0
TRAILING_STOP = 0.992
TIME_EXIT_CANDLES = 10
