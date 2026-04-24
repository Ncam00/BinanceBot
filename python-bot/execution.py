from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET

from config import API_KEY, API_SECRET
from logger import logger

client = Client(API_KEY, API_SECRET)


def get_balance(asset):
    try:
        balance = client.get_asset_balance(asset=asset)
        return float(balance["free"])
    except Exception as e:
        logger.error(f"get_balance ERROR ({asset}): {e}")
        return 0.0


def round_qty(symbol, qty):
    try:
        info = client.get_symbol_info(symbol)
        step_size = float(
            [f for f in info["filters"] if f["filterType"] == "LOT_SIZE"][0]["stepSize"]
        )
        return round(qty - (qty % step_size), 8)
    except Exception as e:
        logger.error(f"round_qty ERROR ({symbol}): {e}")
        return 0.0


def place_market_buy(symbol, qty):
    try:
        order = client.create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        return order
    except Exception as e:
        logger.error(f"place_market_buy ERROR ({symbol}): {e}")
        return None


def place_market_sell(symbol):
    try:
        asset = symbol.replace("USDT", "")
        balance = get_balance(asset)
        qty = round_qty(symbol, balance * 0.999)

        if qty <= 0:
            logger.warning(f"place_market_sell: zero qty for {symbol}, skipping")
            return None

        order = client.create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        return order
    except Exception as e:
        logger.error(f"place_market_sell ERROR ({symbol}): {e}")
        return None
