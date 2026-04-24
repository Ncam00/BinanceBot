import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from logger import logger


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=5)
    except Exception as e:
        logger.error(f"Telegram send ERROR: {e}")
