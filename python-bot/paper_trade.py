import time
import json
import os
from datetime import datetime, timezone
from binance.client import Client
from config import (
    API_KEY, API_SECRET, TRADING_PAIRS, POSITION_SIZE_PCT, DAILY_TRADE_LIMIT,
    ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER, TRAILING_STOP, TIME_EXIT_CANDLES
)
from utils import get_klines, analyze_market, detect_range, detect_breakout, calc_atr

client = Client(API_KEY, API_SECRET)

PAPER_BALANCE = 1000.0
LOOP_INTERVAL = 60
TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_log.jsonl")


def _log_trade(record: dict):
    with open(TRADE_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


class PaperTrader:

    def __init__(self):
        self.positions = {}
        self.balance = PAPER_BALANCE
        self.trades_today = 0
        self.daily_pnl = 0.0

    def log(self, msg):
        print(f"[PAPER] {msg}", flush=True)

    def enter_trade(self, symbol, df, size, tag):
        if symbol in self.positions:
            return
        if self.trades_today >= DAILY_TRADE_LIMIT:
            self.log(f"Daily limit reached, skipping {symbol}")
            return
        trade_value = self.balance * POSITION_SIZE_PCT * size
        price = df["close"].iloc[-1]
        qty = round(trade_value / price, 6)
        if qty <= 0:
            return
        atr = calc_atr(df)
        sl = price - atr * ATR_SL_MULTIPLIER
        tp = price + atr * ATR_TP_MULTIPLIER
        self.positions[symbol] = {
            "entry": price, "qty": qty, "sl": sl, "tp": tp,
            "max_price": price, "candles": 0, "added": False, "cost": trade_value, "tag": tag
        }
        self.balance -= trade_value
        self.trades_today += 1
        self.log(f"BUY  {symbol} | {tag} | Entry: {price:.2f} | SL: {sl:.2f} | TP: {tp:.2f} | Cost: ${trade_value:.2f} | Balance: ${self.balance:.2f}")
        _log_trade({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "tag": tag,
            "entry": price,
            "qty": qty,
            "sl": sl,
            "tp": tp,
            "source": "paper",
            "status": "open"
        })

    def add_position(self, symbol, df):
        pos = self.positions.get(symbol)
        if not pos or pos["added"]:
            return
        price = df["close"].iloc[-1]
        if price <= pos["entry"]:
            return
        trade_value = self.balance * POSITION_SIZE_PCT * 0.7
        qty = round(trade_value / price, 6)
        pos["qty"] += qty
        pos["cost"] += trade_value
        pos["added"] = True
        self.balance -= trade_value
        self.log(f"ADD  {symbol} | Price: {price:.2f} | Qty: {qty} | Balance: ${self.balance:.2f}")

    def exit_trade(self, symbol, reason, price):
        pos = self.positions.get(symbol)
        if not pos:
            return
        proceeds = pos["qty"] * price
        profit = proceeds - pos["cost"]
        self.balance += proceeds
        self.daily_pnl += profit
        self.log(f"{reason} {symbol} | Exit: {price:.2f} | PnL: ${profit:.2f} | Daily PnL: ${self.daily_pnl:.2f} | Balance: ${self.balance:.2f}")
        _log_trade({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "tag": pos.get("tag", ""),
            "entry": pos["entry"],
            "exit": price,
            "qty": pos["qty"],
            "pnl_usd": round(profit, 6),
            "reason": reason.strip(),
            "source": "paper",
            "status": "closed"
        })
        self.positions.pop(symbol, None)

    def manage_trade(self, symbol, df):
        if symbol not in self.positions:
            return
        pos = self.positions[symbol]
        price = df["close"].iloc[-1]
        pos["candles"] += 1
        if price <= pos["sl"]:
            self.exit_trade(symbol, "STOP LOSS  ", price)
            return
        if price >= pos["tp"]:
            self.exit_trade(symbol, "TAKE PROFIT", price)
            return
        if price > pos["max_price"]:
            pos["max_price"] = price
        if price <= pos["max_price"] * TRAILING_STOP:
            self.exit_trade(symbol, "TRAILING   ", price)
            return
        if pos["candles"] >= TIME_EXIT_CANDLES:
            self.exit_trade(symbol, "TIME EXIT  ", price)
            return
        pnl_pct = (price - pos["entry"]) / pos["entry"] * 100
        self.log(f"HOLD {symbol} | Price: {price:.2f} | PnL: {pnl_pct:+.2f}% | Candle: {pos['candles']}/{TIME_EXIT_CANDLES} | SL: {pos['sl']:.2f} | TP: {pos['tp']:.2f}")

    def run(self):
        for symbol in TRADING_PAIRS:
            try:
                df = get_klines(client, symbol)
            except Exception as e:
                self.log(f"ERROR {symbol}: {e}")
                continue
            is_range, range_high, _ = detect_range(df)
            trend, momentum, volume = analyze_market(df)
            score = sum([trend, momentum, volume])
            self.log(f"SCAN {symbol} | Price: {df['close'].iloc[-1]:.2f} | Score: {score}/3 | Range: {is_range} | Trend: {trend} | Mom: {momentum} | Vol: {volume}")
            if symbol not in self.positions:
                if is_range and detect_breakout(df, range_high) and score >= 2:
                    self.enter_trade(symbol, df, 1.0, "BREAKOUT")
                elif is_range and score >= 2:
                    self.enter_trade(symbol, df, 0.3, "SCOUT_RANGE")
            else:
                if score == 3:
                    self.add_position(symbol, df)
                self.manage_trade(symbol, df)


if __name__ == "__main__":
    print(f"=== PAPER TRADING | Virtual Balance: ${PAPER_BALANCE:.2f} ===", flush=True)
    trader = PaperTrader()
    cycle = 0
    while True:
        try:
            cycle += 1
            print(f"\n--- Cycle {cycle} ---", flush=True)
            trader.run()
            print(f"--- Balance: ${trader.balance:.2f} | Open: {list(trader.positions.keys())} ---", flush=True)
            time.sleep(LOOP_INTERVAL)
        except KeyboardInterrupt:
            print(f"\n=== STOPPED | Final Balance: ${trader.balance:.2f} | Daily PnL: ${trader.daily_pnl:.2f} ===")
            break
        except Exception as e:
            print(f"ERROR: {e}", flush=True)
            time.sleep(10)
