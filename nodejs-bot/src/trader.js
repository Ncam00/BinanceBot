/**
 * Trader Engine
 * =============
 * Orchestrates trading operations
 */

const chalk = require('chalk');

class Trader {
    constructor(exchange, strategy, riskManager, db, config) {
        this.exchange = exchange;
        this.strategy = strategy;
        this.riskManager = riskManager;
        this.db = db;
        this.config = config;
        
        this.openPositions = [];
        this.symbolInfo = {};
        this.existingHoldings = []; // Track holdings we found on Binance
        
        // ════════════════════════════════════════════════════════════════════
        // SMARTER THRESHOLDS - Be selective on entries, patient on exits!
        // ════════════════════════════════════════════════════════════════════
        // Higher buy threshold = fewer but better entries
        this.buySignalThreshold = config.buySignalThreshold || 0.55;
        // Higher sell threshold = only sell on STRONG bearish signals
        this.sellSignalThreshold = config.sellSignalThreshold || 0.70;
        
        // CRITICAL: Minimum hold time before signal-based selling (5 minutes)
        this.minHoldTime = 5 * 60 * 1000;
        
        // CRITICAL: Only sell on signals if position is in profit
        this.onlySellInProfit = true;
        
        // Trailing stop settings (conservative)
        this.trailingStopEnabled = config.trailingStopEnabled !== false;
        this.trailingStopActivation = config.trailingStopActivation || 1.5; // Activate after 1.5% gain
        this.trailingStopCallback = config.trailingStopCallback || 0.8;   // 0.8% trailing distance
    }
    
    /**
     * Get open positions
     */
    getOpenPositions() {
        return this.openPositions;
    }
    
    /**
     * Load symbol info for all trading pairs
     */
    async loadSymbolInfo() {
        for (const symbol of this.config.tradingPairs) {
            const info = await this.exchange.getSymbolInfo(symbol);
            if (info) {
                this.symbolInfo[symbol] = info;
            }
        }
    }
    
    /**
     * Sync existing holdings from Binance (track assets we already own)
     */
    async syncExistingHoldings() {
        if (this.exchange.paperMode) return;
        
        try {
            const portfolio = await this.exchange.getTotalBalanceUSDT();
            this.existingHoldings = [];
            
            for (const holding of portfolio.holdings) {
                // Skip stablecoins
                if (['USDT', 'BUSD', 'USDC', 'FDUSD'].includes(holding.asset)) continue;
                
                // Check if this asset is in our trading pairs
                const symbol = `${holding.asset}USDT`;
                if (this.config.tradingPairs.includes(symbol) && holding.valueUSDT > 5) {
                    // Get symbol info to check if we can actually trade this amount
                    const info = await this.exchange.getSymbolInfo(symbol);
                    if (!info) continue;
                    
                    const validQty = this.exchange.roundQuantity(holding.free, info.stepSize);
                    
                    // Skip if quantity is below minimum lot size
                    if (validQty < info.minQty) {
                        console.log(chalk.gray(`   ⚠️ ${holding.asset}: Amount ${holding.free.toFixed(8)} below min lot size ${info.minQty}, skipping`));
                        continue;
                    }
                    
                    // Check if notional value meets minimum (usually $5-10)
                    const currentPrice = await this.exchange.getPrice(symbol);
                    const notionalValue = validQty * currentPrice;
                    if (notionalValue < (info.minNotional || 5)) {
                        console.log(chalk.gray(`   ⚠️ ${holding.asset}: Value $${notionalValue.toFixed(2)} below min notional $${info.minNotional || 5}, skipping`));
                        continue;
                    }
                    
                    // Add as existing position (we don't know entry price, use current)
                    const existingPos = {
                        symbol,
                        side: 'BUY',
                        amount: validQty, // Use validated quantity
                        entryPrice: currentPrice, // Unknown, estimate as current
                        currentPrice: currentPrice,
                        stopLoss: currentPrice * (1 - this.config.stopLossPercent / 100),
                        takeProfit: currentPrice * (1 + this.config.takeProfitPercent / 100),
                        timestamp: Date.now(),
                        isExisting: true, // Flag to know this came from existing holdings
                        signal: { strength: 0, reasons: ['Imported from existing holdings'] }
                    };
                    
                    // Check if not already tracked
                    const alreadyTracked = this.openPositions.some(p => p.symbol === symbol);
                    if (!alreadyTracked) {
                        this.openPositions.push(existingPos);
                        this.existingHoldings.push(existingPos);
                        console.log(chalk.cyan(`   📥 Synced: ${validQty.toFixed(8)} ${holding.asset} (~$${holding.valueUSDT.toFixed(2)}) - tradeable`));
                    }
                }
            }
            
            if (this.existingHoldings.length > 0) {
                console.log(chalk.cyan(`\n   ✓ Synced ${this.existingHoldings.length} tradeable holdings\n`));
            } else {
                console.log(chalk.yellow(`\n   ⚠️ No existing holdings meet tradeable requirements\n`));
            }
            
        } catch (error) {
            console.error(chalk.red(`   Error syncing holdings: ${error.message}`));
        }
    }
    
    /**
     * Execute a full trading cycle
     */
    async executeTradingCycle() {
        const results = {
            signals: [],
            trades: [],
            errors: []
        };
        
        // Load symbol info if not loaded
        if (Object.keys(this.symbolInfo).length === 0) {
            await this.loadSymbolInfo();
        }
        
        // Sync existing holdings first time
        if (this.existingHoldings.length === 0 && !this.exchange.paperMode) {
            await this.syncExistingHoldings();
        }
        
        // Update balance
        const balance = await this.exchange.getBalance();
        this.riskManager.updateBalance(balance);
        
        // Analyze each trading pair
        for (const symbol of this.config.tradingPairs) {
            try {
                // Get candle data
                const candles = await this.exchange.getCandles(
                    symbol, 
                    this.config.candleInterval, 
                    100
                );
                
                if (candles.length < 50) {
                    results.errors.push({ symbol, error: 'Insufficient candle data' });
                    continue;
                }
                
                // Analyze
                const signal = this.strategy.analyze(candles);
                signal.symbol = symbol;
                results.signals.push(signal);
                
                // Log analysis
                if (signal.action !== 'HOLD') {
                    console.log(chalk.gray(
                        `   ${symbol}: ${signal.action} signal (strength: ${signal.strength.toFixed(2)}, ` +
                        `buy: ${signal.buySignals}, sell: ${signal.sellSignals})`
                    ));
                    signal.reasons.forEach(reason => {
                        console.log(chalk.gray(`     - ${reason}`));
                    });
                }
                
                // Check if we have an existing position for this symbol
                const existingPosition = this.openPositions.find(p => p.symbol === symbol);
                
                // Execute SELL if we have a position and signal is STRONGLY bearish
                // BUT: Only sell on signals if PROFITABLE or hold time exceeded (patience!)
                if (existingPosition && signal.action === 'SELL' && signal.strength >= this.sellSignalThreshold) {
                    const currentPrice = signal.indicators.price;
                    const pnlPercent = ((currentPrice - existingPosition.entryPrice) / existingPosition.entryPrice) * 100;
                    const holdTime = Date.now() - existingPosition.timestamp;
                    const holdMinutes = Math.round(holdTime / 60000);
                    
                    // ════════════════════════════════════════════════════════════════
                    // SMART SELL LOGIC: Don't panic sell at a loss!
                    // ════════════════════════════════════════════════════════════════
                    const isInProfit = pnlPercent > 0.1; // At least +0.1% profit
                    const hasHeldLongEnough = holdTime >= this.minHoldTime;
                    const isVeryStrongSignal = signal.strength >= 0.85; // Emergency exit signal
                    
                    if (isInProfit) {
                        // Take profit on bearish signal
                        console.log(chalk.green(`\n   💰 SIGNAL SELL (IN PROFIT) ${symbol}: +${pnlPercent.toFixed(2)}% after ${holdMinutes}min`));
                        const trade = await this.executeSell(existingPosition, 'SIGNAL_PROFIT');
                        if (trade) results.trades.push(trade);
                        continue;
                    } else if (isVeryStrongSignal && hasHeldLongEnough) {
                        // Only exit at loss if VERY strong bearish signal AND held long enough
                        console.log(chalk.yellow(`\n   ⚠️ EMERGENCY SELL ${symbol}: strength ${signal.strength.toFixed(2)}, held ${holdMinutes}min`));
                        const trade = await this.executeSell(existingPosition, 'STRONG_SIGNAL');
                        if (trade) results.trades.push(trade);
                        continue;
                    } else {
                        // HOLD - don't sell at a loss on weak/medium signals
                        console.log(chalk.gray(`   ${symbol}: HOLDING despite sell signal (P&L: ${pnlPercent.toFixed(2)}%, held ${holdMinutes}min, need profit or stronger signal)`));
                        continue;
                    }
                }
                
                // Execute BUY if signal is strong enough and we don't hold it
                if (signal.action === 'BUY' && signal.strength >= this.buySignalThreshold && !existingPosition) {
                    console.log(chalk.cyan(`\n   📈 BUY signal for ${symbol} (strength: ${signal.strength.toFixed(2)} >= ${this.buySignalThreshold})`));
                    const trade = await this.executeBuy(symbol, signal);
                    if (trade) {
                        results.trades.push(trade);
                    }
                }
                
            } catch (error) {
                results.errors.push({ symbol, error: error.message });
            }
        }
        
        return results;
    }
    
    /**
     * Execute a buy order
     */
    async executeBuy(symbol, signal) {
        // Check if we can trade
        const canTrade = this.riskManager.canTrade(this.openPositions, symbol);
        if (!canTrade.allowed) {
            console.log(chalk.yellow(`   ⚠️ Cannot buy ${symbol}: ${canTrade.reason}`));
            return null;
        }
        
        // Get current price and balance
        const balance = await this.exchange.getBalance();
        const price = signal.indicators.price;
        
        // Calculate position size
        const symbolInfo = this.symbolInfo[symbol];
        const quantity = this.riskManager.calculatePositionSize(balance, price, symbolInfo);
        
        if (quantity <= 0) {
            console.log(chalk.yellow(`   ⚠️ Position size too small for ${symbol}`));
            return null;
        }
        
        try {
            // Execute the buy
            const order = await this.exchange.marketBuy(symbol, quantity);
            
            // Track position
            const position = {
                symbol,
                side: 'BUY',
                amount: order.amount,
                entryPrice: order.price,
                currentPrice: order.price,
                stopLoss: this.riskManager.getStopLossPrice(order.price),
                takeProfit: this.riskManager.getTakeProfitPrice(order.price),
                timestamp: Date.now(),
                signal: signal
            };
            
            this.openPositions.push(position);
            
            // Log to database
            this.db.logTrade({
                symbol,
                side: 'BUY',
                amount: order.amount,
                price: order.price,
                value: order.cost,
                orderId: order.orderId,
                paper: order.paper,
                signalStrength: signal.strength,
                reasons: signal.reasons.join(', ')
            });
            
            console.log(chalk.green(
                `\n   ✅ BUY ${order.amount.toFixed(6)} ${symbol} @ $${order.price.toFixed(2)}`
            ));
            console.log(chalk.gray(
                `      Stop-Loss: $${position.stopLoss.toFixed(2)} | Take-Profit: $${position.takeProfit.toFixed(2)}`
            ));
            
            return order;
            
        } catch (error) {
            console.error(chalk.red(`   ❌ Buy failed for ${symbol}: ${error.message}`));
            return null;
        }
    }
    
    /**
     * Execute a sell order
     */
    async executeSell(position, reason = 'SIGNAL') {
        try {
            const order = await this.exchange.marketSell(position.symbol, position.amount);
            
            // Calculate P&L
            const pnl = (order.price - position.entryPrice) * position.amount;
            const pnlPercent = ((order.price - position.entryPrice) / position.entryPrice) * 100;
            
            // Record with risk manager
            this.riskManager.recordTrade(pnl);
            
            // Remove from open positions
            const idx = this.openPositions.findIndex(p => 
                p.symbol === position.symbol && p.timestamp === position.timestamp
            );
            if (idx !== -1) {
                this.openPositions.splice(idx, 1);
            }
            
            // Log to database
            this.db.logTrade({
                symbol: position.symbol,
                side: 'SELL',
                amount: order.amount,
                price: order.price,
                value: order.revenue,
                orderId: order.orderId,
                paper: order.paper,
                pnl: pnl,
                pnlPercent: pnlPercent,
                closeReason: reason
            });
            
            const pnlColor = pnl >= 0 ? chalk.green : chalk.red;
            console.log(pnlColor(
                `\n   📤 SELL ${order.amount.toFixed(6)} ${position.symbol} @ $${order.price.toFixed(2)} ` +
                `(${reason}) | P&L: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPercent >= 0 ? '+' : ''}${pnlPercent.toFixed(2)}%)`
            ));
            
            return { ...order, pnl, pnlPercent, reason };
            
        } catch (error) {
            console.error(chalk.red(`   ❌ Sell failed for ${position.symbol}: ${error.message}`));
            return null;
        }
    }
    
    /**
     * Check positions for stop-loss/take-profit/trailing-stop
     */
    async checkPositions() {
        // Update paper position prices
        if (this.exchange.paperMode) {
            await this.exchange.updatePaperPositionPrices();
        }
        
        for (const position of [...this.openPositions]) {
            try {
                // Get current price
                const currentPrice = await this.exchange.getPrice(position.symbol);
                position.currentPrice = currentPrice;
                position.pnl = (currentPrice - position.entryPrice) * position.amount;
                position.pnlPercent = ((currentPrice - position.entryPrice) / position.entryPrice) * 100;
                
                // Track position age
                const positionAge = Date.now() - position.timestamp;
                
                // ════════════════════════════════════════════════════════════════
                // QUICK PROFIT TAKING - Small gains add up!
                // ════════════════════════════════════════════════════════════════
                if (this.riskManager.shouldTakeQuickProfit(position.pnlPercent, positionAge)) {
                    const profitUSD = position.pnl;
                    console.log(chalk.green(
                        `\n   💰 QUICK PROFIT for ${position.symbol} ` +
                        `(+$${profitUSD.toFixed(2)} USD after ${Math.round(positionAge / 60000)}min)`
                    ));
                    await this.executeSell(position, 'QUICK_PROFIT');
                    continue;
                }
                
                // ════════════════════════════════════════════════════════════════
                // DYNAMIC TAKE PROFIT - Adjusts based on daily goal progress
                // ════════════════════════════════════════════════════════════════
                const dynamicTakeProfit = this.riskManager.getDynamicTakeProfit(this.config.takeProfitPercent);
                const dynamicStopLoss = this.riskManager.getDynamicStopLoss(this.config.stopLossPercent);
                
                // Update position's dynamic targets
                position.dynamicTakeProfit = position.entryPrice * (1 + dynamicTakeProfit / 100);
                position.dynamicStopLoss = position.entryPrice * (1 - dynamicStopLoss / 100);
                
                // Check dynamic take profit
                if (currentPrice >= position.dynamicTakeProfit) {
                    console.log(chalk.green(
                        `\n   🎯 DYNAMIC TAKE PROFIT for ${position.symbol} ` +
                        `(${position.pnlPercent.toFixed(2)}% >= ${dynamicTakeProfit.toFixed(2)}% target)`
                    ));
                    await this.executeSell(position, 'TAKE_PROFIT');
                    continue;
                }
                
                // Check dynamic stop loss
                if (currentPrice <= position.dynamicStopLoss) {
                    console.log(chalk.red(
                        `\n   🛑 DYNAMIC STOP LOSS for ${position.symbol} ` +
                        `@ $${currentPrice.toFixed(2)} (${dynamicStopLoss.toFixed(2)}% limit)`
                    ));
                    await this.executeSell(position, 'STOP_LOSS');
                    continue;
                }
                
                // ════════════════════════════════════════════════════════════════
                // TRAILING STOP LOGIC - Lock in profits!
                // ════════════════════════════════════════════════════════════════
                if (this.trailingStopEnabled) {
                    // Check if we should activate trailing stop
                    if (position.pnlPercent >= this.trailingStopActivation) {
                        // Initialize trailing stop if not set
                        if (!position.trailingStopActive) {
                            position.trailingStopActive = true;
                            position.highestPrice = currentPrice;
                            position.trailingStopPrice = currentPrice * (1 - this.trailingStopCallback / 100);
                            console.log(chalk.cyan(
                                `   🔒 TRAILING STOP ACTIVATED for ${position.symbol} ` +
                                `(${position.pnlPercent.toFixed(2)}% profit) - Stop @ $${position.trailingStopPrice.toFixed(2)}`
                            ));
                        }
                        
                        // Update trailing stop if price goes higher
                        if (currentPrice > position.highestPrice) {
                            position.highestPrice = currentPrice;
                            const newTrailingStop = currentPrice * (1 - this.trailingStopCallback / 100);
                            if (newTrailingStop > position.trailingStopPrice) {
                                position.trailingStopPrice = newTrailingStop;
                                console.log(chalk.cyan(
                                    `   📈 TRAILING STOP RAISED for ${position.symbol} ` +
                                    `- New stop @ $${position.trailingStopPrice.toFixed(2)} (profit: ${position.pnlPercent.toFixed(2)}%)`
                                ));
                            }
                        }
                        
                        // Check if trailing stop was hit
                        if (position.trailingStopActive && currentPrice <= position.trailingStopPrice) {
                            console.log(chalk.yellow(
                                `\n   🛑 TRAILING STOP HIT for ${position.symbol} @ $${currentPrice.toFixed(2)} ` +
                                `(locked profit: ${((position.trailingStopPrice - position.entryPrice) / position.entryPrice * 100).toFixed(2)}%)`
                            ));
                            await this.executeSell(position, 'TRAILING_STOP');
                            continue;
                        }
                    }
                }
                
                // Check standard exit conditions (stop-loss / take-profit)
                const exitCheck = this.strategy.checkExitConditions(
                    position,
                    currentPrice,
                    dynamicStopLoss,
                    dynamicTakeProfit
                );
                
                if (exitCheck.shouldExit) {
                    await this.executeSell(position, exitCheck.reason);
                    continue;
                }
                
                // Also check if strategy signals sell (use lower threshold)
                const candles = await this.exchange.getCandles(position.symbol, this.config.candleInterval, 100);
                if (candles.length >= 50) {
                    const signal = this.strategy.analyze(candles);
                    if (signal.action === 'SELL' && signal.strength >= this.sellSignalThreshold) {
                        await this.executeSell(position, 'SIGNAL');
                    }
                }
                
            } catch (error) {
                console.error(chalk.red(`   Error checking position ${position.symbol}: ${error.message}`));
            }
        }
    }
    
    /**
     * Force close all positions
     */
    async closeAllPositions(reason = 'MANUAL') {
        console.log(chalk.yellow(`\n🔄 Closing all ${this.openPositions.length} positions...`));
        
        for (const position of [...this.openPositions]) {
            await this.executeSell(position, reason);
        }
    }
}

module.exports = Trader;
