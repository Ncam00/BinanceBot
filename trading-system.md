# Smart Trader V2

## Objective

Trade one high-liquidity pair with strict filters, low frequency, and hard daily limits.

Primary goal:
- Protect capital first
- Take only high-quality ETH setups
- Target small consistent daily gains instead of constant activity

## Market Scope

- Pair: ETHUSDT only
- Max open positions: 1
- Max trades per day: 2 absolute max
- Cooldown between trades: 30 minutes

## Session Rules

Timezone reference: New Zealand time.

- Asia: 11:00-19:00
  - Mode: low risk
  - Max session trades: 1
  - Min signal strength: 0.85
  - Risk per trade: 1.0%
- London: 19:00-03:00
  - Mode: normal
  - Max session trades: 2
  - Min signal strength: 0.75
  - Risk per trade: 1.5%
- US: 03:00-11:00
  - Mode: aggressive
  - Max session trades: 2
  - Min signal strength: 0.70
  - Risk per trade: 1.5%

## Hard Risk Limits

- Daily profit lock: $5.00
- Asia profit lock: $3.00
- Max daily loss: $10.00
- Position value cap: 25% of account
- Minimum order value: $10.00

If profit target or max daily loss is hit, trading stops for the day.

## Core Strategy

This is a location-based system. It does not trade in the middle of a range.

The bot first determines:
- Support and resistance
- Market type: RANGE, TREND, or MIXED
- Trade zone: support side, resistance side, or middle

Hard location block:
- If price is not near support or resistance, do not trade
- If price is in the middle zone, do not trade

## Entry Logic

### Base Filters

A buy setup must pass all of these:

- Price is near dynamic support from the last 20 candles
- Distance to support is within 0.3%
- RSI is below 55
- MACD is above signal
- MACD is rising versus previous MACD
- Price is above EMA trend filter
- Confirmation candle is present
- Signal strength meets session threshold

### Market-Type Behavior

- RANGE market: mean reversion logic near support/resistance
- TREND market: breakout/trend-following logic
- MIXED market: no trade, wait for clarity

## Position Sizing

Position size is calculated from:
- Account balance
- Risk percent by session
- Entry price
- Stop-loss distance

Formula:
- Risk amount = account balance x risk percent
- Position size = risk amount / stop distance

Then capped by:
- Max 25% account exposure
- Binance minimum order value

## Exit Logic

- Stop loss is set below support with a 0.5% support buffer
- Stop loss is never wider than 3% from entry
- Take profit is set at 2:1 reward-to-risk
- Open positions are checked continuously for stop loss or take profit hits

## Operational Guards

The bot checks these before scanning for trades:

- No open position already exists
- Daily hard trade limit is not reached
- Cooldown has expired
- Daily profit target is not hit
- Daily loss limit is not hit
- Session trade limit is not hit

If any guard fails, the bot waits and does not force trades.

## Notifications

Telegram alerts are sent for:
- Bot start
- Buy execution
- Sell execution with P&L
- Daily trading stop due to profit lock or max loss

No alert means the bot is still scanning and has not found a valid setup.

## Key Principles

- One coin only
- One position only
- No middle-of-range trades
- No revenge trading
- No overtrading
- No discretionary overrides
- Quality over quantity

## System Summary

This strategy is best described as conservative-selective.

It is designed to:
- Trade infrequently
- Filter aggressively
- Preserve capital
- Let strong setups come to the bot instead of forcing activity