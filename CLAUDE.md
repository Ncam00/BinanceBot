# BinanceBot — Claude Code Context

## What this repo is
A rule-based (NOT AI/ML) crypto trading bot for Binance. It uses technical indicators
(RSI, MACD, EMA, ATR, ADX, Bollinger Bands) to enter long positions on BTC and ETH.
The bot is written in Python and trades via the Binance REST API.

## Active bot file
`python-bot/smart_trader_v3_live.py` — this is the only file that runs live.
Do NOT touch `nodejs-bot/` or `main.py` — they are legacy/unused.

## Key constants (lines 47–90 of smart_trader_v3_live.py)
| Constant | Value | Meaning |
|---|---|---|
| `DRY_RUN` | `False` | **LIVE mode — real money**. Set `True` to paper trade. |
| `RISK_PER_TRADE` | `0.01` | 1% of balance risked per trade |
| `POSITION_USDT_TARGET` | `60.0` | ~$60 position size per trade |
| `ATR_SL_MULTIPLIER` | `1.5` | Stop loss = entry − ATR × 1.5 |
| `ATR_TP_MULTIPLIER` | `2.0` | Take profit target = 2× stop distance |
| `PARTIAL_TP_RATIO` | `0.5` | 50% exits at TP1, remainder runs |
| `A_PLUS_ONLY` | `False` | Allow B+ entries at 60% position size (relaxed from True) |
| `KILL_TRADE_CANDLES` | `10` | Exit losing trade after 10 candles AND −1% |
| `MAX_TRADES_PER_DAY` | `3` | Hard cap across all sessions |
| `MIN_VOLUME_MULTIPLIER` | `1.05` | Volume vs avg to confirm signal (was 1.1) |
| `adx_range_threshold` | `18` | ADX < 18 = choppy, skip (was 22) |
| Daily range filter | `> 85%` | Block longs above 85% of daily range (was 75%) |
| UTC trading window | `24/7` | Removed — session-slot caps prevent overtrading |

## Daily risk limits (set in __init__, line ~553)
- **Daily profit target:** $7.00 — bot stops opening new trades when hit
- **Daily loss limit:** $7.00 — bot stops if daily loss reaches this
- **EU session cap:** 2 trades max (reserves a slot for US session)
- **US session cap:** 1 trade + any unused EU slots

## Session definitions (NZST)
- Asia: 11:00–19:00 → max 1 trade
- EU/London: 19:00–03:00 → max 2 trades
- US: 03:00–11:00 → max 1–3 trades (depends on EU usage)

## Entry types (confidence tiers)
- **A+** — highest confidence, full $60 position (only type taken when A_PLUS_ONLY=True)
- **B+** — medium confidence, 60% position size
- **SCOUT / SCOUT_RANGE** — low confidence, 40% position size (currently skipped)

## Risk rules — DO NOT override without asking
1. Never increase RISK_PER_TRADE above 0.02 (2%)
2. Never remove the daily loss limit check in can_trade()
3. Never set DRY_RUN = False in a response without explicit user confirmation
4. Never widen ATR_SL_MULTIPLIER beyond 2.0 — stops must be tight enough to respect the 1% risk rule
5. The EU/US session slot caps exist intentionally — don't remove them

## Diagnostic tools
```bash
python analyze_trades.py          # full loss diagnostic from trades.db
python analyze_trades.py --days 7 # last 7 days only
python project_returns.py         # compound returns projection for 750 NZD
python dashboard.py               # web dashboard at http://localhost:8050
```

## Database
- Path: `data/trades.db` (SQLite)
- Tables: `trades`, `signals`, `balance_history`
- Written to by `smart_trader_v3_live.py` during live/paper runs
- Read by `analyze_trades.py` and `dashboard.py`

## Key methods to know
| Method | Location | Purpose |
|---|---|---|
| `can_trade()` | line ~2394 | Master gate — checks all limits before any entry |
| `process_pair()` | line ~135 | Signal scoring and entry logic per pair |
| `get_daily_context()` | line ~720 | Fetches 24h high/low, blocks entries near daily top |
| `get_market_session()` | line ~668 | Returns current session (london/us/asia) |
| `check_daily_reset()` | line ~2251 | Resets daily counters at midnight |
| `execute_buy()` | line ~1480 | Places the actual order (skipped if DRY_RUN) |

## Trading pairs
BTCUSDT, ETHUSDT (SOL removed — lower liquidity)

## What has already been fixed (don't revert)
- Trailing stop now only activates after TP1 (was cutting winners pre-TP1)
- Trailing width is ATR-based (was fixed 1% — too tight for crypto)
- Daily profit cap raised from $5 → $7 (now matches the loss limit)
- 24h daily range filter blocks entries when price is in top 25% of day's range
- Per-session trade slots (EU/US) prevent EU session burning all daily trades
- A_PLUS_ONLY = True filters out low-confidence setups
