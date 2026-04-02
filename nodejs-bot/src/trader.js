/**
 * Trader Engine
 * =============
 * Orchestrates trading operations with Advanced Analysis
 * NEW: DCA, Momentum Scalping, Dynamic Position Sizing
 */

const chalk = require('chalk');
const AdvancedAnalysis = require('./advancedAnalysis');
const DCAManager = require('./dcaManager');
const MomentumScalper = require('./momentumScalper');

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
        
        // Advanced Analysis Module (Order Book, Multi-TF, Support/Resistance)
        this.advancedAnalysis = new AdvancedAnalysis(exchange);
        this.useAdvancedAnalysis = config.useAdvancedAnalysis !== false; // ON by default
        
        // ═══════════════════════════════════════════════════════════════════
        // NEW FEATURES: DCA + Momentum Scalping
        // ═══════════════════════════════════════════════════════════════════
        this.dcaManager = new DCAManager(config);
        this.momentumScalper = new MomentumScalper(exchange, config);
        
        // ═══════════════════════════════════════════════════════════════════
        // PORTFOLIO FLOOR PROTECTION - Never go below this value!
        // ═══════════════════════════════════════════════════════════════════
        this.portfolioFloor = config.portfolioFloor || 327; // ~$50 NZD below start
        this.floorProtectionActive = false;
        
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
        
        // Market sentiment tracking for smart profit-taking
        this.marketSentiment = 'neutral'; // bullish, neutral, bearish
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
     * Check portfolio floor protection
     */
    async checkFloorProtection() {
        try {
            const portfolio = await this.exchange.getTotalBalanceUSDT();
            const totalValue = portfolio.totalUSDT;
            
            if (totalValue < this.portfolioFloor) {
                if (!this.floorProtectionActive) {
                    console.log(chalk.red.bold(`\n   🛑 FLOOR PROTECTION ACTIVATED!`));
                    console.log(chalk.red(`   Portfolio: $${totalValue.toFixed(2)} < Floor: $${this.portfolioFloor}`));
                    console.log(chalk.red(`   All new BUY orders BLOCKED until portfolio recovers\n`));
                    this.floorProtectionActive = true;
                }
                return true; // Floor breached
            } else {
                if (this.floorProtectionActive) {
                    console.log(chalk.green(`\n   ✅ Floor protection deactivated - Portfolio recovered to $${totalValue.toFixed(2)}\n`));
                }
                this.floorProtectionActive = false;
                return false; // OK
            }
        } catch (error) {
            return false; // On error, allow trading
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
        
        // CHECK FLOOR PROTECTION FIRST
        await this.checkFloorProtection();
        
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
                
                // ════════════════════════════════════════════════════════════════════
                // DCA CHECK: If we're losing on this position, consider averaging down
                // ════════════════════════════════════════════════════════════════════
                if (existingPosition && !this.floorProtectionActive) {
                    const currentPrice = signal.indicators.price;
                    const pnlPercent = ((currentPrice - existingPosition.entryPrice) / existingPosition.entryPrice) * 100;
                    
                    // Only DCA on losing positions with bullish/neutral signals
                    if (pnlPercent < -2 && signal.action !== 'SELL') {
                        const dcaResult = this.dcaManager.shouldDCA(existingPosition, currentPrice);
                        
                        if (dcaResult.shouldDCA) {
                            console.log(chalk.blue(`\n   📉 DCA OPPORTUNITY ${symbol}: Price down ${Math.abs(pnlPercent).toFixed(2)}%`));
                            console.log(chalk.blue(`      Reason: ${dcaResult.reason}`));
                            
                            // Execute DCA buy
                            const dcaTrade = await this.executeDCABuy(existingPosition, signal);
                            if (dcaTrade) {
                                results.trades.push(dcaTrade);
                                console.log(chalk.green(`   ✅ DCA executed - new avg entry adjusted`));
                            }
                        }
                    }
                }
                
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
                    
                    // Check Support/Resistance for sell decision
                    let nearResistance = false;
                    if (this.useAdvancedAnalysis && isInProfit) {
                        try {
                            const sr = await this.advancedAnalysis.detectSupportResistance(symbol);
                            nearResistance = sr.nearResistance;
                            if (nearResistance && pnlPercent > 0.5) {
                                console.log(chalk.yellow(`   📊 Near resistance level - good time to take profit!`));
                            }
                        } catch (e) {}
                    }
                    
                    if (isInProfit || (isInProfit && nearResistance)) {
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
                
                // ════════════════════════════════════════════════════════════════════
                // ADVANCED BUY LOGIC with Order Book, Multi-TF, Support/Resistance
                // ════════════════════════════════════════════════════════════════════
                if (signal.action === 'BUY' && signal.strength >= this.buySignalThreshold && !existingPosition) {
                    
                    // BLOCK BUYS if floor protection is active
                    if (this.floorProtectionActive) {
                        console.log(chalk.red(`   🛑 BUY BLOCKED [${symbol}] - Floor protection active`));
                        continue;
                    }
                    
                    // ════════════════════════════════════════════════════════════
                    // MOMENTUM SCALP CHECK: Boost signal for momentum plays
                    // ════════════════════════════════════════════════════════════
                    let momentumBoost = 0;
                    try {
                        const momentum = await this.momentumScalper.detectMomentum(symbol, candles);
                        if (momentum.hasMomentum) {
                            momentumBoost = 0.15; // +15% signal strength boost
                            console.log(chalk.magenta(`   🚀 MOMENTUM detected ${symbol}: ${momentum.reason}`));
                            console.log(chalk.magenta(`      Signal boosted: ${signal.strength.toFixed(2)} → ${(signal.strength + momentumBoost).toFixed(2)}`));
                        }
                    } catch (e) {
                        // Momentum check optional, continue without it
                    }
                    
                    // Adjust signal strength with momentum
                    const adjustedSignal = { ...signal, strength: signal.strength + momentumBoost };
                    
                    // Use Advanced Analysis if enabled
                    let buyConfirmed = true;
                    let advancedReasons = [];
                    
                    if (this.useAdvancedAnalysis) {
                        try {
                            const advanced = await this.advancedAnalysis.getAdvancedSignal(symbol, this.strategy);
                            
                            // Check for blockers (sell wall is a warning, not a hard block)
                            if (advanced.analysis.orderBook.hasWall) {
                                advancedReasons.push('⚠️ Sell wall nearby - caution');
                                // Only block if wall + bearish timeframes together
                                if (advanced.analysis.multiTimeframe.signal === 'bearish') {
                                    buyConfirmed = false;
                                    advancedReasons.push('❌ Sell wall + bearish timeframes - blocked');
                                }
                            }
                            
                            // Check multi-timeframe alignment
                            if (advanced.analysis.multiTimeframe.signal === 'bearish') {
                                buyConfirmed = false;
                                advancedReasons.push('❌ Higher timeframes bearish');
                            }
                            
                            // Boost if near support
                            if (advanced.analysis.supportResistance.nearSupport) {
                                advancedReasons.push('✅ Near support - good entry');
                            }
                            
                            // Boost if all timeframes aligned bullish
                            if (advanced.analysis.multiTimeframe.aligned && advanced.analysis.multiTimeframe.signal === 'bullish') {
                                advancedReasons.push('✅ All timeframes bullish');
                            }
                            
                            // Log advanced analysis
                            if (advancedReasons.length > 0) {
                                console.log(chalk.blue(`   🔬 Advanced Analysis for ${symbol}:`));
                                advancedReasons.forEach(r => console.log(chalk.blue(`      ${r}`)));
                            }
                            
                        } catch (error) {
                            // On error, proceed with basic signal
                            console.log(chalk.gray(`   ⚠️ Advanced analysis unavailable, using basic signal`));
                        }
                    }
                    
                    if (buyConfirmed) {
                        console.log(chalk.cyan(`\n   📈 BUY signal for ${symbol} (strength: ${signal.strength.toFixed(2)} >= ${this.buySignalThreshold})`));
                        const trade = await this.executeBuy(symbol, signal);
                        if (trade) {
                            results.trades.push(trade);
                        }
                    } else {
                        console.log(chalk.yellow(`   ⚠️ BUY signal for ${symbol} REJECTED by advanced analysis`));
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
        
        // Calculate position size with DYNAMIC sizing based on signal strength
        const symbolInfo = this.symbolInfo[symbol];
        const signalStrength = signal.strength || 0.5;
        const quantity = this.riskManager.calculatePositionSize(balance, price, symbolInfo, signalStrength);
        
        if (quantity <= 0) {
            console.log(chalk.yellow(`   ⚠️ Position size too small for ${symbol}`));
            return null;
        }
        
        // Log dynamic sizing
        console.log(chalk.gray(`   📊 Dynamic sizing: signal ${signalStrength.toFixed(2)} → qty ${quantity.toFixed(6)}`));
        
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
     * Execute a DCA (Dollar Cost Average) buy to average down on losing position
     */
    async executeDCABuy(existingPosition, signal) {
        const symbol = existingPosition.symbol;
        
        // Check if we can trade
        const canTrade = this.riskManager.canTrade(this.openPositions, symbol, true); // true = DCA exception
        if (!canTrade.allowed && !canTrade.dcaAllowed) {
            console.log(chalk.yellow(`   ⚠️ Cannot DCA ${symbol}: ${canTrade.reason}`));
            return null;
        }
        
        // Get current price and balance
        const balance = await this.exchange.getBalance();
        const currentPrice = signal.indicators.price;
        
        // Calculate DCA amount (smaller than initial, based on DCA multiplier)
        const symbolInfo = this.symbolInfo[symbol];
        const dcaMultiplier = this.config.dcaMultiplier || 1.5;
        
        // Base size, then multiply by DCA factor
        let baseQty = this.riskManager.calculatePositionSize(balance, currentPrice, symbolInfo, 0.6);
        let dcaQty = baseQty * dcaMultiplier;
        
        // Round to step size
        if (symbolInfo && symbolInfo.stepSize) {
            dcaQty = Math.floor(dcaQty / symbolInfo.stepSize) * symbolInfo.stepSize;
        }
        
        if (dcaQty <= 0) {
            console.log(chalk.yellow(`   ⚠️ DCA size too small for ${symbol}`));
            return null;
        }
        
        try {
            // Execute the DCA buy
            const order = await this.exchange.marketBuy(symbol, dcaQty);
            
            // Update existing position with averaged entry
            const totalAmount = existingPosition.amount + order.amount;
            const totalCost = (existingPosition.amount * existingPosition.entryPrice) + (order.amount * order.price);
            const newAvgEntry = totalCost / totalAmount;
            
            // Update the position
            existingPosition.amount = totalAmount;
            existingPosition.entryPrice = newAvgEntry;
            existingPosition.dcaCount = (existingPosition.dcaCount || 0) + 1;
            existingPosition.stopLoss = this.riskManager.getStopLossPrice(newAvgEntry);
            existingPosition.takeProfit = this.riskManager.getTakeProfitPrice(newAvgEntry);
            
            // Track DCA with manager
            this.dcaManager.recordDCA(existingPosition, order.price);
            
            // Log to database
            this.db.logTrade({
                symbol,
                side: 'BUY',
                amount: order.amount,
                price: order.price,
                value: order.cost,
                orderId: order.orderId,
                paper: order.paper,
                reason: 'DCA',
                dcaLevel: existingPosition.dcaCount
            });
            
            console.log(chalk.blue(
                `\n   📉 DCA BUY ${order.amount.toFixed(6)} ${symbol} @ $${order.price.toFixed(2)}`
            ));
            console.log(chalk.blue(
                `      New avg entry: $${newAvgEntry.toFixed(4)} | Total amount: ${totalAmount.toFixed(6)}`
            ));
            
            return order;
            
        } catch (error) {
            console.error(chalk.red(`   ❌ DCA buy failed for ${symbol}: ${error.message}`));
            return null;
        }
    }
    
    /**
     * Execute a sell order
     */
    async executeSell(position, reason = 'SIGNAL') {
        try {
            // Check actual balance to avoid "insufficient balance" errors (fees reduce actual amount)
            let sellAmount = position.amount;
            try {
                const account = await this.exchange.client.accountInfo();
                const asset = position.symbol.replace('USDT', '');
                const assetBalance = account.balances.find(b => b.asset === asset);
                const actualFree = parseFloat(assetBalance?.free || 0);
                if (actualFree < sellAmount && actualFree > 0) {
                    console.log(chalk.yellow(`   ⚠️ Adjusted sell amount for ${position.symbol}: ${sellAmount} → ${actualFree} (fee adjustment)`));
                    sellAmount = actualFree;
                } else if (actualFree <= 0) {
                    console.log(chalk.red(`   ❌ No ${asset} balance to sell, removing stale position`));
                    const idx = this.openPositions.findIndex(p => p.symbol === position.symbol && p.timestamp === position.timestamp);
                    if (idx !== -1) this.openPositions.splice(idx, 1);
                    return null;
                }
            } catch (balErr) {
                console.log(chalk.yellow(`   ⚠️ Could not check balance, using tracked amount`));
            }
            const order = await this.exchange.marketSell(position.symbol, sellAmount);
            
            // Calculate P&L
            const pnl = (order.price - position.entryPrice) * position.amount;
            const pnlPercent = ((order.price - position.entryPrice) / position.entryPrice) * 100;
            
            // Calculate price move for market sentiment
            const priceMove = ((order.price - position.entryPrice) / position.entryPrice) * 100;
            
            // Record with risk manager and update market sentiment
            this.riskManager.recordTrade(pnl);
            if (this.riskManager.updateMarketSentiment) {
                this.riskManager.updateMarketSentiment(pnl, priceMove);
            }
            
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
            
            // Show market sentiment
            const bullish = this.riskManager.isMarketBullish ? this.riskManager.isMarketBullish() : false;
            const marketIndicator = bullish ? chalk.green('📈 BULLISH') : chalk.yellow('📉 NEUTRAL/BEARISH');
            
            const pnlColor = pnl >= 0 ? chalk.green : chalk.red;
            console.log(pnlColor(
                `\n   📤 SELL ${order.amount.toFixed(6)} ${position.symbol} @ $${order.price.toFixed(2)} ` +
                `(${reason}) | P&L: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPercent >= 0 ? '+' : ''}${pnlPercent.toFixed(2)}%)`
            ));
            console.log(chalk.gray(`      Market: ${marketIndicator}`));
            
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
                // 🧠 SMART PROFIT TAKING - Small gains add up!
                // ════════════════════════════════════════════════════════════════
                const smartCheck = this.checkSmartProfitTaking(position);
                
                if (smartCheck.shouldSell) {
                    console.log(chalk.green(
                        `\n   ${smartCheck.reason} for ${position.symbol} ` +
                        `(held ${Math.round(positionAge / 60000)}min)`
                    ));
                    await this.executeSell(position, smartCheck.reason);
                    continue;
                }
                
                // Set tight trailing stop if suggested by smart check
                if (smartCheck.setTrailingStop && !position.trailingStopActive) {
                    position.trailingStopActive = true;
                    position.highestPrice = currentPrice;
                    position.trailingStopPrice = currentPrice * (1 - (smartCheck.trailPercent || 0.4) / 100);
                    console.log(chalk.cyan(
                        `   🔒 SMART TRAILING STOP for ${position.symbol} ` +
                        `(bullish market, +${position.pnlPercent.toFixed(2)}%)`
                    ));
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
     * 🧠 SMART PROFIT-TAKING LOGIC 🧠
     * Based on profit level, time held, and market conditions
     * STRICT RULE: NEVER SELL AT A LOSS (except emergency stop-loss at -8%)
     */
    checkSmartProfitTaking(position) {
        const profitUSD = position.pnl;
        const profitPercent = position.pnlPercent;
        const minutesHeld = (Date.now() - position.timestamp) / 60000;
        const isBullish = this.marketSentiment === 'bullish';
        
        // ═══════════════════════════════════════════════════════════════
        // CRITICAL: Never sell if not in profit (except emergency)
        // ═══════════════════════════════════════════════════════════════
        if (profitUSD <= 0) {
            // Only emergency stop-loss at -8%
            if (profitPercent <= -8) {
                return { shouldSell: true, reason: `EMERGENCY_STOP (-${Math.abs(profitPercent).toFixed(1)}%)` };
            }
            return { shouldSell: false, reason: 'Waiting for profit' };
        }
        
        // ═══════════════════════════════════════════════════════════════
        // SEMI-AGGRESSIVE+ PROFIT RULES - Capture gains FAST!
        // Target: $4-6 USD/day = 12-15 wins @ $0.30-$0.50 each
        // ═══════════════════════════════════════════════════════════════
        
        // $0.50+ profit - TAKE IT NOW! (was $1.00)
        if (profitUSD >= 0.50) {
            return { shouldSell: true, reason: `💰 QUICK_PROFIT +$${profitUSD.toFixed(2)} (≥$0.50 rule)` };
        }
        
        // $0.25+ profit after 1 minute (was $0.50 after 3min)
        if (profitUSD >= 0.25 && minutesHeld >= 1) {
            return { shouldSell: true, reason: `💰 FAST_PROFIT +$${profitUSD.toFixed(2)} after ${minutesHeld.toFixed(1)}min` };
        }
        
        // $0.15+ profit after 90 seconds (was $0.10 after 2min)
        if (profitUSD >= 0.15 && minutesHeld >= 1.5) {
            return { shouldSell: true, reason: `💰 SMART_PROFIT +$${profitUSD.toFixed(2)} (≥$0.15 rule)` };
        }
        
        // $0.10+ profit after 2 minutes (keep this one)
        if (profitUSD >= 0.10 && minutesHeld >= 2) {
            return { shouldSell: true, reason: `💰 SMART_PROFIT +$${profitUSD.toFixed(2)} after ${minutesHeld.toFixed(0)}min` };
        }
        
        // Any profit > $0.03 after 10 minutes (was $0.02 after 15min)
        if (profitUSD > 0.03 && minutesHeld >= 10) {
            return { shouldSell: true, reason: `💰 PATIENCE_PROFIT +$${profitUSD.toFixed(2)} after ${minutesHeld.toFixed(0)}min` };
        }
        
        // In bullish market with decent profit - trail TIGHTER (0.3% vs 0.4%)
        if (isBullish && profitPercent >= 0.6) {
            return { shouldSell: false, setTrailingStop: true, trailPercent: 0.3 };
        }
        
        return { shouldSell: false, reason: 'Holding for more profit' };
    }
    
    /**
     * Update market sentiment based on recent price action
     */
    updateMarketSentiment(priceChanges) {
        const bullishCount = priceChanges.filter(p => p > 0.2).length;
        const bearishCount = priceChanges.filter(p => p < -0.2).length;
        
        if (bullishCount >= priceChanges.length * 0.6) {
            this.marketSentiment = 'bullish';
        } else if (bearishCount >= priceChanges.length * 0.6) {
            this.marketSentiment = 'bearish';
        } else {
            this.marketSentiment = 'neutral';
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
