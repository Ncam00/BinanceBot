import logging
import os

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    filename="logs/trades.log",
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)

# ==============================
# CONFIG
# ==============================

TRADING_PAIRS = ["BTCUSDT", "ETHUSDT"]

ATR_SL_MULTIPLIER = 1.5
ATR_TP_MULTIPLIER = 2.0
TRAILING_STOP = 0.992
TIME_EXIT_CANDLES = 10


class SmartTrader:

    def __init__(self):
        self.positions = {}
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.htf_cache = {}

    # ==============================
    # ANALYSIS
    # ==============================
    def analyze_market(self, df):
        close = df['close']

        ema_fast = close.ewm(span=9).mean()
        ema_slow = close.ewm(span=21).mean()

        trend = ema_fast.iloc[-1] > ema_slow.iloc[-1]
        momentum = close.iloc[-1] > close.iloc[-3]
        volume_spike = df['volume'].iloc[-1] > df['volume'].rolling(20).mean().iloc[-1] * 1.2

        return {
            "trend": trend,
            "momentum": momentum,
            "volume": volume_spike
        }

    # ==============================
    # ENTRY
    # ==============================
    def check_entry(self, symbol, df):

        if symbol in self.positions:
            return

        if self.trades_today >= 3:
            return

        signal = self.analyze_market(df)
        score = sum(signal.values())

        # SCOUT
        if score >= 2:
            self.enter_trade(symbol, df, size=0.3, tag="SCOUT")

        # A+
        if score == 3:
            self.enter_trade(symbol, df, size=0.7, tag="A_PLUS")

    def enter_trade(self, symbol, df, size, tag):

        price = df['close'].iloc[-1]
        atr = (df['high'] - df['low']).rolling(14).mean().iloc[-1]

        sl = price - (atr * ATR_SL_MULTIPLIER)
        tp = price + (atr * ATR_TP_MULTIPLIER)

        self.positions[symbol] = {
            "entry": price,
            "sl": sl,
            "tp": tp,
            "size": size,
            "tag": tag,
            "candles": 0,
            "max_price": price
        }

        self.trades_today += 1
        logging.info(f"ENTER {symbol} | {tag} | Entry: {price} | SL: {sl} | TP: {tp}")

    # ==============================
    # EXIT
    # ==============================
    def manage_trade(self, symbol, df):

        if symbol not in self.positions:
            return

        pos = self.positions[symbol]
        price = df['close'].iloc[-1]

        pos["candles"] += 1

        # STOP LOSS
        if price <= pos["sl"]:
            profit = price - pos["entry"]
            self.daily_pnl += profit
            logging.info(f"STOP LOSS HIT {symbol} | PnL: {profit:.4f} | Daily PnL: {self.daily_pnl:.4f}")
            del self.positions[symbol]
            return

        # TAKE PROFIT
        if price >= pos["tp"]:
            profit = price - pos["entry"]
            self.daily_pnl += profit
            logging.info(f"TAKE PROFIT {symbol} | PnL: {profit:.4f} | Daily PnL: {self.daily_pnl:.4f}")
            del self.positions[symbol]
            return

        # TRAILING
        if price > pos["max_price"]:
            pos["max_price"] = price

        trailing_sl = pos["max_price"] * TRAILING_STOP

        if price <= trailing_sl:
            profit = price - pos["entry"]
            self.daily_pnl += profit
            logging.info(f"TRAILING EXIT {symbol} | PnL: {profit:.4f} | Daily PnL: {self.daily_pnl:.4f}")
            del self.positions[symbol]
            return

        # TIME EXIT
        if pos["candles"] >= TIME_EXIT_CANDLES:
            profit = price - pos["entry"]
            self.daily_pnl += profit
            logging.info(f"TIME EXIT {symbol} | PnL: {profit:.4f} | Daily PnL: {self.daily_pnl:.4f}")
            del self.positions[symbol]

    # ==============================
    # LOOP
    # ==============================
    def run(self, data):

        for symbol in TRADING_PAIRS:
            df = data[symbol]

            self.check_entry(symbol, df)
            self.manage_trade(symbol, df)