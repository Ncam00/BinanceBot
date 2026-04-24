import logging
import os

os.makedirs("logs", exist_ok=True)

logger = logging.getLogger("smart_trader")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.FileHandler("logs/trades.log")
    handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    logger.addHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    logger.addHandler(console)
