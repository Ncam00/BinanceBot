import time

from smart_trader_v3_live import SmartTrader
from logger import logger
from telegram import send_telegram

LOOP_INTERVAL = 60   # seconds between cycles
ERROR_SLEEP = 10     # seconds to wait after an error


def main():
    logger.info("=== Smart Trader V3 LIVE starting ===")
    send_telegram("Smart Trader V3 LIVE started")

    trader = SmartTrader()

    while True:
        try:
            trader.run()
            time.sleep(LOOP_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            send_telegram("Smart Trader V3 LIVE stopped by user")
            break

        except Exception as e:
            msg = f"GLOBAL ERROR: {e}"
            logger.error(msg)
            send_telegram(msg)
            time.sleep(ERROR_SLEEP)


if __name__ == "__main__":
    main()
