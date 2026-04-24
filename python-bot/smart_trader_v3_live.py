from config import (
    TRADING_PAIRS, POSITION_SIZE_PCT, DAILY_TRADE_LIMIT,
    ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER, TRAILING_STOP, TIME_EXIT_CANDLES
)
from execution import client, get_balance, round_qty, place_market_buy, place_market_sell
from utils import get_klines, analyze_market, detect_range, detect_breakout, calc_atr
from logger import logger
from telegram import send_telegram


class SmartTrader:

    def __init__(self):
        self.positions = {}
        self.trades_today = 0
        self.daily_pnl = 0.0

    # ==============================
    # ENTRY
    # ==============================
    def enter_trade(self, symbol, df, size, tag):

        if symbol in self.positions:
            return

        if self.trades_today >= DAILY_TRADE_LIMIT:
            logger.info(f"Daily trade limit reached ({DAILY_TRADE_LIMIT}), skipping {symbol}")
            return

        usdt_balance = get_balance("USDT")
        trade_value = usdt_balance * POSITION_SIZE_PCT * size

        price = df["close"].iloc[-1]
        qty = trade_value / price
        qty = round_qty(symbol, qty)

        if qty <= 0:
            logger.warning(f"enter_trade: zero qty for {symbol}, skipping")
            return

        order = place_market_buy(symbol, qty)

        if order is None:
            msg = f"BUY FAILED {symbol} | {tag}"
            logger.error(msg)
            send_telegram(msg)
            return

        atr = calc_atr(df)

        self.positions[symbol] = {
            "entry": price,
            "qty": qty,
            "sl": price - atr * ATR_SL_MULTIPLIER,
            "tp": price + atr * ATR_TP_MULTIPLIER,
            "max_price": price,
            "candles": 0,
            "added": False
        }

        self.trades_today += 1

        msg = f"BUY {symbol} | {tag} | Entry: {price:.4f} | SL: {self.positions[symbol]['sl']:.4f} | TP: {self.positions[symbol]['tp']:.4f} | Qty: {qty}"
        logger.info(msg)
        send_telegram(msg)

    def add_position(self, symbol, df):

        pos = self.positions.get(symbol)
        if not pos or pos["added"]:
            return

        price = df["close"].iloc[-1]

        if price <= pos["entry"]:
            return

        usdt_balance = get_balance("USDT")
        trade_value = usdt_balance * POSITION_SIZE_PCT * 0.7

        qty = trade_value / price
        qty = round_qty(symbol, qty)

        if qty <= 0:
            return

        order = place_market_buy(symbol, qty)

        if order is None:
            logger.error(f"ADD FAILED {symbol}")
            return

        pos["qty"] += qty
        pos["added"] = True

        msg = f"ADD {symbol} | Price: {price:.4f} | Qty: {qty}"
        logger.info(msg)
        send_telegram(msg)

    # ==============================
    # EXIT
    # ==============================
    def exit_trade(self, symbol, reason):

        pos = self.positions.get(symbol)
        if not pos:
            return

        price_at_exit = pos.get("max_price", pos["entry"])

        order = place_market_sell(symbol)

        profit = price_at_exit - pos["entry"]
        self.daily_pnl += profit

        if order is not None:
            msg = f"{reason} {symbol} | PnL: {profit:.4f} | Daily PnL: {self.daily_pnl:.4f}"
            logger.info(msg)
            send_telegram(msg)
        else:
            msg = f"SELL FAILED {symbol} | {reason}"
            logger.error(msg)
            send_telegram(msg)

        self.positions.pop(symbol, None)

    # ==============================
    # MANAGE
    # ==============================
    def manage_trade(self, symbol, df):

        if symbol not in self.positions:
            return

        pos = self.positions[symbol]
        price = df["close"].iloc[-1]

        pos["candles"] += 1

        if price <= pos["sl"]:
            self.exit_trade(symbol, "STOP LOSS")
            return

        if price >= pos["tp"]:
            self.exit_trade(symbol, "TAKE PROFIT")
            return

        if price > pos["max_price"]:
            pos["max_price"] = price

        trailing_sl = pos["max_price"] * TRAILING_STOP

        if price <= trailing_sl:
            self.exit_trade(symbol, "TRAILING EXIT")
            return

        if pos["candles"] >= TIME_EXIT_CANDLES:
            self.exit_trade(symbol, "TIME EXIT")

    # ==============================
    # RUN
    # ==============================
    def run(self):

        for symbol in TRADING_PAIRS:

            try:
                df = get_klines(client, symbol)
            except Exception as e:
                msg = f"ERROR fetching klines {symbol}: {e}"
                logger.error(msg)
                send_telegram(msg)
                continue

            is_range, range_high, range_low = detect_range(df)
            trend, momentum, volume = analyze_market(df)
            score = sum([trend, momentum, volume])

            # ======================
            # ENTRY
            # ======================
            if symbol not in self.positions:

                # SCOUT inside range
                if is_range and score >= 2:
                    self.enter_trade(symbol, df, 0.3, "SCOUT_RANGE")

                # BREAKOUT ENTRY
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
