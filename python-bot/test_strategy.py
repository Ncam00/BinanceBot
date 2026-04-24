import pandas as pd
import numpy as np
import time
from binance.client import Client
from binance.enums import *

# ==============================
# CONFIG
# ==============================

API_KEY = ""
API_SECRET = ""

TRADING_PAIRS = ["BTCUSDT", "ETHUSDT"]

POSITION_SIZE_PCT = 0.10
TRAILING_STOP = 0.992
TIME_EXIT_CANDLES = 10

ATR_SL_MULTIPLIER = 1.5
ATR_TP_MULTIPLIER = 2.0

client = Client(API_KEY, API_SECRET)


# ==============================
# HELPER FUNCTIONS
# ==============================

def get_balance(asset):
    balance = client.get_asset_balance(asset=asset)
    return float(balance["free"])


def round_qty(symbol, qty):
    info = client.get_symbol_info(symbol)
    step_size = float([f for f in info["filters"] if f["filterType"] == "LOT_SIZE"][0]["stepSize"])
    return round(qty - (qty % step_size), 8)


def get_klines(symbol, interval="5m", limit=100):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)

    df = pd.DataFrame(klines, columns=[
        "time","open","high","low","close","volume",
        "close_time","qav","trades","tb_base","tb_quote","ignore"
    ])

    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["volume"] = df["volume"].astype(float)

    return df


def detect_range(df):
    recent = df[-20:]

    high = recent['high'].max()
    low = recent['low'].min()

    range_size = (high - low) / low

    # tight range = consolidation
    is_range = range_size < 0.01  # 1%

    return is_range, high, low


def detect_breakout(df, range_high):
    close = df['close'].iloc[-1]
    volume = df['volume'].iloc[-1]
    avg_volume = df['volume'].rolling(20).mean().iloc[-1]

    breakout = close > range_high * 1.001  # avoid fakeouts
    volume_confirm = volume > avg_volume * 1.2

    return breakout and volume_confirm


# ==============================
# BOT
# ==============================

class SmartTrader:

    def __init__(self):
        self.positions = {}

    def analyze_market(self, df):
        close = df['close']

        ema_fast = close.ewm(span=9).mean()
        ema_slow = close.ewm(span=21).mean()

        trend = ema_fast.iloc[-1] > ema_slow.iloc[-1]
        momentum = close.iloc[-1] > close.iloc[-3]
        volume = df['volume'].iloc[-1] > df['volume'].rolling(20).mean().iloc[-1] * 1.2

        return trend, momentum, volume

    def enter_trade(self, symbol, df, size, tag):

        usdt_balance = get_balance("USDT")
        trade_value = usdt_balance * POSITION_SIZE_PCT * size

        price = df['close'].iloc[-1]
        qty = trade_value / price
        qty = round_qty(symbol, qty)

        if qty <= 0:
            return

        try:
            client.create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )

            atr = (df['high'] - df['low']).rolling(14).mean().iloc[-1]

            self.positions[symbol] = {
                "entry": price,
                "qty": qty,
                "sl": price - atr * ATR_SL_MULTIPLIER,
                "tp": price + atr * ATR_TP_MULTIPLIER,
                "max_price": price,
                "candles": 0,
                "added": False
            }

            print(f"BUY {symbol} | {tag} | Qty: {qty}")

        except Exception as e:
            print(f"BUY ERROR {symbol}: {e}")

    def add_position(self, symbol, df):

        pos = self.positions[symbol]

        if pos["added"]:
            return

        price = df['close'].iloc[-1]

        if price <= pos["entry"]:
            return

        usdt_balance = get_balance("USDT")
        trade_value = usdt_balance * POSITION_SIZE_PCT * 0.7

        qty = trade_value / price
        qty = round_qty(symbol, qty)

        try:
            client.create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )

            pos["qty"] += qty
            pos["added"] = True

            print(f"ADD {symbol} | Qty: {qty}")

        except Exception as e:
            print(f"ADD ERROR: {e}")

    def exit_trade(self, symbol):

        asset = symbol.replace("USDT", "")
        balance = get_balance(asset)

        qty = round_qty(symbol, balance * 0.999)

        if qty <= 0:
            self.positions.pop(symbol, None)
            return

        try:
            client.create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )

            print(f"SELL {symbol}")
            self.positions.pop(symbol, None)

        except Exception as e:
            print(f"SELL ERROR: {e}")
            self.positions.pop(symbol, None)

    def manage_trade(self, symbol, df):

        if symbol not in self.positions:
            return

        pos = self.positions[symbol]
        price = df['close'].iloc[-1]

        pos["candles"] += 1

        if price <= pos["sl"]:
            print(f"STOP LOSS {symbol}")
            self.exit_trade(symbol)
            return

        if price >= pos["tp"]:
            print(f"TAKE PROFIT {symbol}")
            self.exit_trade(symbol)
            return

        if price > pos["max_price"]:
            pos["max_price"] = price

        trailing = pos["max_price"] * TRAILING_STOP

        if price <= trailing:
            print(f"TRAILING EXIT {symbol}")
            self.exit_trade(symbol)
            return

        if pos["candles"] >= TIME_EXIT_CANDLES:
            print(f"TIME EXIT {symbol}")
            self.exit_trade(symbol)

    def run(self):

        for symbol in TRADING_PAIRS:

            df = get_klines(symbol)

            # Detect range
            is_range, range_high, range_low = detect_range(df)

            trend, momentum, volume = self.analyze_market(df)
            score = sum([trend, momentum, volume])

            # ======================
            # ENTRY
            # ======================
            if symbol not in self.positions:

                # SCOUT inside range (optional early entry)
                if is_range and score >= 2:
                    self.enter_trade(symbol, df, 0.3, "SCOUT_RANGE")

                # BREAKOUT ENTRY (THIS IS THE MONEY)
                elif is_range:
                    if detect_breakout(df, range_high):
                        self.enter_trade(symbol, df, 1.0, "BREAKOUT")

            # ======================
            # MANAGE POSITION
            # ======================
            else:
                if score == 3:
                    self.add_position(symbol, df)

                self.manage_trade(symbol, df)


# ==============================
# MAIN LOOP
# ==============================

if __name__ == "__main__":

    trader = SmartTrader()

    while True:
        try:
            trader.run()
            time.sleep(60)
        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(10)
