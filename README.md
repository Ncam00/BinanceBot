# BinanceBot — Smart Trader V3

Automated spot trading bot for BTC/ETH on Binance. Targets high-probability setups during EU and US sessions with a 3-stage exit system designed to lock partial profit early and let winners run.

---

## Features

- **Session filtering** — only trades EU (7–11pm NZST) and US (1–5am NZST) sessions
- **ATR volatility gate** — skips setups where ATR < 0.8% of price (no chop)
- **6-point entry scoring** — trend, momentum, volume, breakout, volatility expansion, trend strength
- **Fixed $60 USDT position sizing** — consistent risk every trade
- **True net 1:2 R:R** — take-profit is fee-adjusted so the net reward is actually 2R
- **3-stage exit** — TP1 at 1R (40%), TP2 at 2R (30%), runner trails at 3% below max
- **Paper / dry-run mode** — test with no real orders before going live
- **Telegram alerts** — every entry, exit, and stats update sent to your phone
- **Live stats** — win rate, expectancy, avg win/loss printed after every trade

---

## Requirements

- Python 3.10+
- Binance account with Spot API key enabled
- Telegram bot token + chat ID
- Dependencies: `pip install -r requirements.txt`

---

## Setup

1. **Clone the repo**
   ```bash
   git clone <repo-url>
   cd BinanceBot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Create a `.env` file** in the repo root:
   ```
   BINANCE_API_KEY=your_api_key
   BINANCE_API_SECRET=your_api_secret
   TELEGRAM_TOKEN=your_telegram_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

4. **Confirm paper mode is on** (it is by default)
   In `python-bot/smart_trader_v3_live.py`, line 66:
   ```python
   DRY_RUN = True   # change to False when ready to go live
   ```

---

## Running

```bash
cd python-bot
python smart_trader_v3_live.py
```

The bot prints a startup banner and sends a Telegram message confirming paper or live mode.

---

## Key Config

All constants are at the top of `python-bot/smart_trader_v3_live.py`:

| Constant | Default | Description |
|---|---|---|
| `DRY_RUN` | `True` | Paper mode — no real orders placed |
| `TRADING_PAIRS` | `['BTCUSDT', 'ETHUSDT']` | Symbols to trade |
| `POSITION_USDT_TARGET` | `60.0` | Position size in USDT |
| `MAX_TRADES_PER_DAY` | `3` | Hard cap on daily entries |
| `ATR_SL_MULTIPLIER` | `1.5` | Stop loss = ATR × 1.5 below entry |
| `TRAILING_STOP` | `0.985` | 1.5% trail before TP1 hit |
| `RUNNER_TRAIL` | `0.970` | 3% trail after TP1 — lets winners run |
| `TIME_EXIT_CANDLES` | `25` | Exit stalled trade after 25 candles (~6h) |
| `FEE_RATE` | `0.001` | 0.1% per side (Binance spot) |

---

## Trading Sessions (NZST)

| Session | Hours (NZST) | Why |
|---|---|---|
| EU | 7:00pm – 11:00pm | London open — highest BTC/ETH volume |
| US | 1:00am – 5:00am | New York open — second-highest volume |

The bot returns immediately outside these windows. Asia session is excluded due to low volatility and high fee risk.

---

## Exit System

| Stage | Trigger | Action | Position Remaining |
|---|---|---|---|
| TP1 | price ≥ entry + 1R (fee-adjusted) | Sell 40%, SL → breakeven | 60% |
| TP2 | price ≥ entry + 2R (fee-adjusted) | Sell 50% of remaining | 30% |
| Runner | price drops 3% below session high | Exit remaining 30% | 0% |

Before TP1 is hit, the trailing stop is tight (1.5%) to cut losers fast. After TP1 the trail widens to 3% so the runner can reach 3R–4R or beyond.

---

## Risk Warning

This bot trades real money. Always run in paper mode (`DRY_RUN = True`) for at least 2–3 sessions before going live. Past performance in testing does not guarantee future results. This is not financial advice.
