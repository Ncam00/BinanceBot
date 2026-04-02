/**
 * Risk Manager
 * ============
 * Handles position sizing, risk limits, and circuit breakers
 * 
 * PROFIT PROTECTION: Only trades with initial capital, profits are kept separate
 */

const chalk = require('chalk');
const fs = require('fs');
const path = require('path');

class RiskManager {
    constructor(config, initialBalance) {
        this.config = config;
        this.currentBalance = initialBalance;
        
        // ═══════════════════════════════════════════════════════════════════
        // PROFIT PROTECTION SYSTEM
        // ═══════════════════════════════════════════════════════════════════
        // Load or set initial capital (the locked trading amount)
        this.capitalFile = path.join(__dirname, '../data/capital.json');
        this.capitalData = this.loadCapitalData(initialBalance);
        
        // Initial capital = the LOCKED amount we trade with (never use profits for trading)
        this.initialCapital = this.capitalData.initialCapital;
        this.lockedTradingCapital = this.capitalData.lockedTradingCapital;
        
        // Profits are tracked separately and NOT used for trading
        this.totalRealizedProfit = this.capitalData.totalRealizedProfit;
        this.withdrawableProfit = this.capitalData.withdrawableProfit;
        this.profitWithdrawn = this.capitalData.profitWithdrawn;
        
        // Legacy compatibility
        this.initialBalance = this.lockedTradingCapital;
        
        // Risk parameters
        this.maxPositionSizePercent = config.maxPositionSizePercent || 10;
        this.maxConcurrentPositions = config.maxConcurrentPositions || 3;
        this.stopLossPercent = config.stopLossPercent || 3;
        this.takeProfitPercent = config.takeProfitPercent || 5;
        this.dailyLossLimitPercent = config.dailyLossLimitPercent || 5;
        
        // ═══════════════════════════════════════════════════════════════════
        // DAILY PROFIT GOAL TRACKING (NZD)
        // Target: $2-$10 NZD daily = ~$1.20-$6 USD
        // ═══════════════════════════════════════════════════════════════════
        this.dailyProfitGoalMinNZD = config.dailyProfitGoalMin || 2;
        this.dailyProfitGoalMaxNZD = config.dailyProfitGoalMax || 10;
        this.nzdToUsdRate = 0.60; // Approximate NZD to USD conversion
        this.dailyProfitGoalMinUSD = this.dailyProfitGoalMinNZD * this.nzdToUsdRate;
        this.dailyProfitGoalMaxUSD = this.dailyProfitGoalMaxNZD * this.nzdToUsdRate;
        this.goalHitToday = false;
        this.conservativeMode = false; // After hitting min goal, be more careful
        
        // Small profit tracking
        this.todaysProfits = [];
        this.smallProfitTarget = 0.50; // $0.50-$3 per trade
        this.maxProfitPerTrade = 3.00;
        
        // Daily tracking
        this.dailyStartBalance = this.lockedTradingCapital;
        this.dailyPnL = 0;
        this.dailyTrades = 0;
        this.consecutiveLosses = 0;
        this.lastResetDate = new Date().toDateString();
        
        // Circuit breaker state
        this.isPaused = false;
        this.pauseReason = null;
        this.pauseUntil = null;
        
        // Alert tracking
        this.lastProfitAlert = 0;
        this.profitAlertThreshold = 5; // Alert every $5 profit
    }
    
    /**
     * Load capital tracking data from file
     */
    loadCapitalData(currentBalance) {
        try {
            if (fs.existsSync(this.capitalFile)) {
                const data = JSON.parse(fs.readFileSync(this.capitalFile, 'utf8'));
                console.log(chalk.cyan('\n═══════════════════════════════════════════════════════════'));
                console.log(chalk.cyan('  💰 PROFIT PROTECTION SYSTEM LOADED'));
                console.log(chalk.cyan('═══════════════════════════════════════════════════════════'));
                console.log(chalk.white(`  Initial Capital (locked): $${data.lockedTradingCapital.toFixed(2)}`));
                console.log(chalk.green(`  Withdrawable Profit:      $${data.withdrawableProfit.toFixed(2)}`));
                console.log(chalk.gray(`  Total Realized Profit:    $${data.totalRealizedProfit.toFixed(2)}`));
                console.log(chalk.gray(`  Already Withdrawn:        $${data.profitWithdrawn.toFixed(2)}`));
                console.log(chalk.cyan('═══════════════════════════════════════════════════════════\n'));
                return data;
            }
        } catch (e) {
            console.log(chalk.yellow('  ⚠️ Could not load capital data, creating new...'));
        }
        
        // First run - set initial capital
        const newData = {
            initialCapital: currentBalance,
            lockedTradingCapital: currentBalance,
            totalRealizedProfit: 0,
            withdrawableProfit: 0,
            profitWithdrawn: 0,
            createdAt: new Date().toISOString()
        };
        
        this.saveCapitalData(newData);
        
        console.log(chalk.cyan('\n═══════════════════════════════════════════════════════════'));
        console.log(chalk.cyan('  💰 PROFIT PROTECTION SYSTEM INITIALIZED'));
        console.log(chalk.cyan('═══════════════════════════════════════════════════════════'));
        console.log(chalk.white(`  Locked Trading Capital: $${currentBalance.toFixed(2)}`));
        console.log(chalk.yellow(`  ⚠️ Bot will ONLY trade with this amount`));
        console.log(chalk.green(`  ✅ All profits will be kept separate for withdrawal`));
        console.log(chalk.cyan('═══════════════════════════════════════════════════════════\n'));
        
        return newData;
    }
    
    /**
     * Save capital tracking data
     */
    saveCapitalData(data = null) {
        const saveData = data || {
            initialCapital: this.initialCapital,
            lockedTradingCapital: this.lockedTradingCapital,
            totalRealizedProfit: this.totalRealizedProfit,
            withdrawableProfit: this.withdrawableProfit,
            profitWithdrawn: this.profitWithdrawn,
            lastUpdated: new Date().toISOString()
        };
        
        try {
            fs.writeFileSync(this.capitalFile, JSON.stringify(saveData, null, 2));
        } catch (e) {
            console.error(chalk.red('Failed to save capital data:', e.message));
        }
    }
    
    /**
     * Update current balance
     */
    updateBalance(balance) {
        this.currentBalance = balance;
        
        // Calculate current profit (balance minus locked capital)
        const currentProfit = balance - this.lockedTradingCapital;
        
        // If we have unrealized profit above threshold, update withdrawable
        if (currentProfit > this.withdrawableProfit) {
            // Only update withdrawable when we have MORE than before
            // This keeps profit locked even during drawdowns
        }
    }
    
    /**
     * Reset daily tracking (call at start of new day)
     */
    resetDaily() {
        const today = new Date().toDateString();
        if (today !== this.lastResetDate) {
            this.dailyStartBalance = this.lockedTradingCapital; // Use locked capital, not total
            this.dailyPnL = 0;
            this.dailyTrades = 0;
            this.consecutiveLosses = 0;
            this.lastResetDate = today;
            
            // Reset daily goal tracking
            this.goalHitToday = false;
            this.conservativeMode = false;
            this.todaysProfits = [];
            
            console.log(chalk.cyan('\n═══════════════════════════════════════════════════════════'));
            console.log(chalk.cyan('  📅 NEW TRADING DAY STARTED'));
            console.log(chalk.cyan('═══════════════════════════════════════════════════════════'));
            console.log(chalk.white(`  Daily Profit Goal: $${this.dailyProfitGoalMinNZD}-$${this.dailyProfitGoalMaxNZD} NZD`));
            console.log(chalk.gray(`  (Equivalent: ~$${this.dailyProfitGoalMinUSD.toFixed(2)}-$${this.dailyProfitGoalMaxUSD.toFixed(2)} USD)`));
            console.log(chalk.cyan('═══════════════════════════════════════════════════════════\n'));
            
            // Reset pause if it was time-based
            if (this.pauseUntil && new Date() > this.pauseUntil) {
                this.isPaused = false;
                this.pauseReason = null;
                this.pauseUntil = null;
            }
        }
    }
    
    /**
     * Calculate position size for a trade
     * IMPORTANT: Uses the MINIMUM of locked capital OR actual available balance
     * NEW: Dynamic sizing based on signal strength!
     */
    calculatePositionSize(balance, price, symbolInfo = null, signalStrength = 0.5) {
        // ═══════════════════════════════════════════════════════════════════
        // PROFIT PROTECTION: Use minimum of locked capital or actual balance
        // ═══════════════════════════════════════════════════════════════════
        // Use actual available balance if less than locked capital
        const tradableBalance = Math.min(this.lockedTradingCapital, balance);
        
        // ═══════════════════════════════════════════════════════════════════
        // DYNAMIC POSITION SIZING based on signal strength!
        // Stronger signals = bigger positions, weaker signals = smaller
        // ═══════════════════════════════════════════════════════════════════
        let dynamicSizePercent = this.maxPositionSizePercent;
        
        if (signalStrength >= 0.70) {
            // VERY STRONG signal: 1.3x normal size
            dynamicSizePercent = this.maxPositionSizePercent * 1.3;
        } else if (signalStrength >= 0.55) {
            // STRONG signal: Normal size
            dynamicSizePercent = this.maxPositionSizePercent;
        } else if (signalStrength >= 0.45) {
            // MEDIUM signal: 0.7x normal size
            dynamicSizePercent = this.maxPositionSizePercent * 0.7;
        } else {
            // WEAK signal: 0.5x normal size
            dynamicSizePercent = this.maxPositionSizePercent * 0.5;
        }
        
        // Max position is X% of tradable balance (now dynamic!)
        const maxPositionValue = tradableBalance * (dynamicSizePercent / 100);
        
        // Calculate quantity
        let quantity = maxPositionValue / price;
        
        // Apply symbol constraints if available
        if (symbolInfo) {
            // Round to step size
            const stepSize = symbolInfo.stepSize || 0.00001;
            const precision = Math.max(0, Math.round(-Math.log10(stepSize)));
            quantity = Math.floor(quantity * Math.pow(10, precision)) / Math.pow(10, precision);
            
            // Ensure above minimum
            if (quantity < symbolInfo.minQty) {
                return 0; // Cannot meet minimum
            }
            
            // Ensure value above minimum notional
            if (quantity * price < symbolInfo.minNotional) {
                return 0;
            }
        }
        
        return quantity;
    }
    
    /**
     * Check if a trade is allowed under current risk rules
     * @param {boolean} isDCA - If true, this is a DCA buy on existing position
     */
    canTrade(openPositions, symbol, isDCA = false) {
        this.resetDaily();
        
        // Check if paused
        if (this.isPaused) {
            if (this.pauseUntil && new Date() > this.pauseUntil) {
                this.isPaused = false;
                this.pauseReason = null;
            } else {
                return {
                    allowed: false,
                    dcaAllowed: false,
                    reason: `Trading paused: ${this.pauseReason}`
                };
            }
        }
        
        // Check max concurrent positions (skip for DCA)
        if (!isDCA && openPositions.length >= this.maxConcurrentPositions) {
            return {
                allowed: false,
                reason: `Max positions reached (${this.maxConcurrentPositions})`
            };
        }
        
        // Check if already in position for this symbol
        // For DCA, we WANT to be in position already
        const existingPosition = openPositions.find(p => p.symbol === symbol);
        if (!isDCA && existingPosition) {
            return {
                allowed: false,
                reason: `Already in position for ${symbol}`
            };
        }
        
        // For DCA, check max DCA levels (default 2)
        if (isDCA && existingPosition) {
            const maxDCALevels = this.config.maxDCALevels || 2;
            const currentDCACount = existingPosition.dcaCount || 0;
            if (currentDCACount >= maxDCALevels) {
                return {
                    allowed: false,
                    dcaAllowed: false,
                    reason: `Max DCA levels reached (${currentDCACount}/${maxDCALevels})`
                };
            }
            // DCA is allowed
            return { allowed: true, dcaAllowed: true };
        }
        
        // Check daily loss limit
        const dailyLossPercent = (this.dailyPnL / this.dailyStartBalance) * 100;
        if (dailyLossPercent <= -this.dailyLossLimitPercent) {
            this.pause(`Daily loss limit hit (${dailyLossPercent.toFixed(2)}%)`, 24 * 60);
            return {
                allowed: false,
                reason: `Daily loss limit exceeded (${dailyLossPercent.toFixed(2)}%)`
            };
        }
        
        // Check consecutive losses (5 losses = short cooldown, then reset)
        if (this.consecutiveLosses >= 5) {
            this.consecutiveLosses = 0; // Reset counter so bot resumes after cooldown
            this.pause('5 consecutive losses - short cooldown', 10);
            return {
                allowed: false,
                reason: '5 consecutive losses - 10 min cooldown'
            };
        }
        
        return { allowed: true };
    }
    
    /**
     * Record a trade result
     * PROFIT PROTECTION: Profits go to withdrawable pool, not back into trading
     */
    recordTrade(pnl) {
        this.dailyPnL += pnl;
        this.dailyTrades++;
        
        if (pnl < 0) {
            this.consecutiveLosses++;
            // Loss comes from trading capital
            this.lockedTradingCapital += pnl; // Reduce capital by loss
        } else {
            this.consecutiveLosses = 0;
            
            // Track this profit
            this.todaysProfits.push({
                amount: pnl,
                time: new Date().toISOString()
            });
            
            // ═══════════════════════════════════════════════════════════════════
            // PROFIT PROTECTION: Profit goes to WITHDRAWABLE, not back to trading
            // ═══════════════════════════════════════════════════════════════════
            this.totalRealizedProfit += pnl;
            this.withdrawableProfit += pnl;
            
            // Don't add profit back to trading capital - it stays separate!
            // this.lockedTradingCapital stays the same for profits
            
            // Check daily goal progress
            const dailyProfitNZD = this.dailyPnL / this.nzdToUsdRate;
            const progressPercent = (this.dailyPnL / this.dailyProfitGoalMinUSD) * 100;
            
            console.log(chalk.green('\n═══════════════════════════════════════════════════════════'));
            console.log(chalk.green('  💰 PROFIT REALIZED!'));
            console.log(chalk.green('═══════════════════════════════════════════════════════════'));
            console.log(chalk.white(`  This Trade:            +$${pnl.toFixed(2)} USD`));
            console.log(chalk.white(`  Today's Total:         +$${this.dailyPnL.toFixed(2)} USD (~$${dailyProfitNZD.toFixed(2)} NZD)`));
            console.log(chalk.cyan(`  Daily Goal Progress:   ${progressPercent.toFixed(0)}% of $${this.dailyProfitGoalMinNZD} NZD`));
            console.log(chalk.green(`  Total Withdrawable:    $${this.withdrawableProfit.toFixed(2)} USD`));
            
            // Check if we hit daily goal
            if (this.dailyPnL >= this.dailyProfitGoalMinUSD && !this.goalHitToday) {
                this.goalHitToday = true;
                this.conservativeMode = true;
                console.log(chalk.bgGreen.black('\n  🎉 DAILY MINIMUM GOAL HIT! ($' + this.dailyProfitGoalMinNZD + ' NZD)'));
                console.log(chalk.yellow('  Now in CONSERVATIVE MODE - protecting profits'));
            }
            
            if (this.dailyPnL >= this.dailyProfitGoalMaxUSD) {
                console.log(chalk.bgGreen.black('\n  🏆 DAILY MAXIMUM GOAL HIT! ($' + this.dailyProfitGoalMaxNZD + ' NZD)'));
                console.log(chalk.yellow('  Consider pausing to lock in profits!'));
            }
            
            console.log(chalk.green('═══════════════════════════════════════════════════════════\n'));
        }
        
        // Update total balance tracking
        this.currentBalance = this.lockedTradingCapital + this.withdrawableProfit;
        
        // Save to file
        this.saveCapitalData();
        
        // Check if we should alert about withdrawable profits
        this.checkProfitAlert();
    }
    
    /**
     * Check if we should alert user about withdrawable profits
     */
    checkProfitAlert() {
        if (this.withdrawableProfit >= this.profitAlertThreshold && 
            this.withdrawableProfit - this.lastProfitAlert >= this.profitAlertThreshold) {
            
            this.lastProfitAlert = this.withdrawableProfit;
            
            console.log(chalk.bgGreen.black('\n' + '═'.repeat(60)));
            console.log(chalk.bgGreen.black('  🎉 PROFIT ALERT: You have money ready to withdraw!'));
            console.log(chalk.bgGreen.black('═'.repeat(60)));
            console.log(chalk.green(`\n  Withdrawable Balance: $${this.withdrawableProfit.toFixed(2)} USDT`));
            console.log(chalk.white('\n  To withdraw:'));
            console.log(chalk.gray('  1. Open Binance app'));
            console.log(chalk.gray('  2. Go to Wallet → Spot → USDT'));
            console.log(chalk.gray('  3. Tap Withdraw'));
            console.log(chalk.gray(`  4. Send $${this.withdrawableProfit.toFixed(2)} to your bank/external wallet`));
            console.log(chalk.yellow('\n  ⚠️ Bot will continue trading with locked capital only'));
            console.log(chalk.green('\n' + '═'.repeat(60) + '\n'));
        }
    }
    
    /**
     * Mark profits as withdrawn (call after manual withdrawal)
     */
    markProfitWithdrawn(amount) {
        if (amount > this.withdrawableProfit) {
            amount = this.withdrawableProfit;
        }
        
        this.withdrawableProfit -= amount;
        this.profitWithdrawn += amount;
        this.currentBalance = this.lockedTradingCapital + this.withdrawableProfit;
        
        this.saveCapitalData();
        
        console.log(chalk.cyan(`\n  ✅ Marked $${amount.toFixed(2)} as withdrawn`));
        console.log(chalk.cyan(`     Remaining withdrawable: $${this.withdrawableProfit.toFixed(2)}`));
    }
    
    /**
     * Pause trading
     */
    pause(reason, durationMinutes = null) {
        this.isPaused = true;
        this.pauseReason = reason;
        
        if (durationMinutes) {
            this.pauseUntil = new Date(Date.now() + durationMinutes * 60 * 1000);
        }
        
        console.log(`⚠️ Trading paused: ${reason}`);
        if (this.pauseUntil) {
            console.log(`   Will resume at: ${this.pauseUntil.toLocaleTimeString()}`);
        }
    }
    
    /**
     * Resume trading
     */
    resume() {
        this.isPaused = false;
        this.pauseReason = null;
        this.pauseUntil = null;
    }
    
    /**
     * Get current risk status
     */
    getStatus() {
        const dailyPnLPercent = this.dailyStartBalance > 0 
            ? (this.dailyPnL / this.dailyStartBalance) * 100 
            : 0;
            
        const totalPnLPercent = this.initialCapital > 0 
            ? (this.totalRealizedProfit / this.initialCapital) * 100 
            : 0;
        
        // Daily goal progress
        const dailyProfitNZD = this.dailyPnL / this.nzdToUsdRate;
        const goalProgress = (this.dailyPnL / this.dailyProfitGoalMinUSD) * 100;
        
        return {
            isPaused: this.isPaused,
            pauseReason: this.pauseReason,
            pauseUntil: this.pauseUntil,
            
            // Profit protection info
            initialCapital: this.initialCapital,
            lockedTradingCapital: this.lockedTradingCapital,
            withdrawableProfit: this.withdrawableProfit,
            totalRealizedProfit: this.totalRealizedProfit,
            profitWithdrawn: this.profitWithdrawn,
            
            // Daily goal tracking
            dailyProfitGoalMinNZD: this.dailyProfitGoalMinNZD,
            dailyProfitGoalMaxNZD: this.dailyProfitGoalMaxNZD,
            dailyProfitNZD: dailyProfitNZD,
            goalProgress: goalProgress,
            goalHitToday: this.goalHitToday,
            conservativeMode: this.conservativeMode,
            todaysProfits: this.todaysProfits,
            
            // Legacy fields
            currentBalance: this.currentBalance,
            initialBalance: this.initialCapital,
            
            dailyPnL: this.dailyPnL,
            dailyPnLPercent,
            dailyTrades: this.dailyTrades,
            dailyLossLimit: this.dailyLossLimitPercent,
            
            totalPnL: this.totalRealizedProfit,
            totalPnLPercent,
            
            consecutiveLosses: this.consecutiveLosses,
            maxPositionSizePercent: this.maxPositionSizePercent,
            maxConcurrentPositions: this.maxConcurrentPositions,
        };
    }
    
    /**
     * Get dynamic take-profit based on goal progress and market conditions
     * - Before goal: Take smaller profits (1-1.5%)
     * - After min goal: Be more conservative (0.8-1%)
     * - After max goal: Very conservative (0.5%)
     */
    getDynamicTakeProfit(basePercent) {
        if (this.dailyPnL >= this.dailyProfitGoalMaxUSD) {
            // Hit max goal - be very conservative
            return Math.min(basePercent, 0.5);
        }
        if (this.conservativeMode) {
            // Hit min goal - be conservative
            return Math.min(basePercent, 0.8);
        }
        // Haven't hit goal yet - normal take profit for small gains
        return Math.min(basePercent, 1.2);
    }
    
    /**
     * Get dynamic stop-loss based on goal progress
     * Tighter stops after hitting goals to protect profits
     */
    getDynamicStopLoss(basePercent) {
        if (this.dailyPnL >= this.dailyProfitGoalMaxUSD) {
            // Hit max goal - very tight stop
            return Math.min(basePercent, 0.5);
        }
        if (this.conservativeMode) {
            // Hit min goal - tighter stop
            return Math.min(basePercent, 1.0);
        }
        // Normal stop loss
        return basePercent;
    }
    
    /**
     * Track market sentiment (bullish/bearish) based on recent trades
     */
    updateMarketSentiment(pnl, priceMove) {
        if (!this.recentTrades) this.recentTrades = [];
        this.recentTrades.push({ pnl, priceMove, timestamp: Date.now() });
        
        // Keep last 10 trades / 30 min of data
        const cutoff = Date.now() - 30 * 60 * 1000;
        this.recentTrades = this.recentTrades.filter(t => t.timestamp > cutoff).slice(-10);
    }
    
    /**
     * Check if market appears bullish (prices generally going up)
     */
    isMarketBullish() {
        if (!this.recentTrades || this.recentTrades.length < 3) return false;
        
        // Count winning trades
        const wins = this.recentTrades.filter(t => t.pnl > 0).length;
        const winRate = wins / this.recentTrades.length;
        
        // Bullish if >60% wins recently
        return winRate > 0.6;
    }
    
    /**
     * Get recent loss rate (0-1) for bearish market guard
     * Returns ratio of losing trades in last 30 min
     */
    getRecentLossRate() {
        if (!this.recentTrades || this.recentTrades.length < 3) return 0;
        const losses = this.recentTrades.filter(t => t.pnl < 0).length;
        return losses / this.recentTrades.length;
    }
    
    /**
     * Check if we should take a quick profit on current position
     * SMART: Takes small profits quickly OR trails in bullish markets
     */
    shouldTakeQuickProfit(currentProfitPercent, positionAge, currentPrice = null, highestPrice = null) {
        const ageMinutes = positionAge / 60000;
        
        // ═══════════════════════════════════════════════════════════════════
        // TIER 1: Very small profits held long enough
        // ═══════════════════════════════════════════════════════════════════
        
        // If profit > $0.10 (0.03%) and held > 2 minutes, consider taking it
        if (currentProfitPercent >= 0.03 && ageMinutes >= 2) {
            // Check if we're in a bullish market - if so, let it ride with trailing
            if (this.isMarketBullish() && currentProfitPercent < 1.5) {
                // Don't quick-exit in bullish markets unless trailing stop would lock in profit
                return false;
            }
            
            // Take the small profit
            if (currentProfitPercent >= 0.15) {
                return true; // ~$0.20+ profit
            }
        }
        
        // ═══════════════════════════════════════════════════════════════════
        // TIER 2: Decent profits at any age
        // ═══════════════════════════════════════════════════════════════════
        
        // Take 0.4%+ profit after 3+ minutes
        if (currentProfitPercent >= 0.4 && ageMinutes >= 3) {
            return true; // ~$0.50+ profit
        }
        
        // Take 0.8%+ profit immediately (good trade!)
        if (currentProfitPercent >= 0.8) {
            return true; // ~$1+ profit
        }
        
        // ═══════════════════════════════════════════════════════════════════
        // TIER 3: Old positions with any profit
        // ═══════════════════════════════════════════════════════════════════
        
        // Position older than 15 min with ANY profit > 0.1%
        if (ageMinutes > 15 && currentProfitPercent > 0.1) {
            return true;
        }
        
        // Position older than 30 min - take whatever profit we have
        if (ageMinutes > 30 && currentProfitPercent > 0) {
            return true;
        }
        
        // ═══════════════════════════════════════════════════════════════════
        // TIER 4: Conservative mode adjustments
        // ═══════════════════════════════════════════════════════════════════
        
        if (this.conservativeMode && currentProfitPercent >= 0.3) {
            return true; // Lower threshold when we've already hit daily goal
        }
        
        if (this.dailyPnL >= this.dailyProfitGoalMaxUSD && currentProfitPercent > 0.05) {
            return true; // Hit max goal, take any profit
        }
        
        return false;
    }
    
    /**
     * Calculate stop-loss price
     */
    getStopLossPrice(entryPrice, side = 'BUY') {
        if (side === 'BUY') {
            return entryPrice * (1 - this.stopLossPercent / 100);
        }
        return entryPrice * (1 + this.stopLossPercent / 100);
    }
    
    /**
     * Calculate take-profit price
     */
    getTakeProfitPrice(entryPrice, side = 'BUY') {
        if (side === 'BUY') {
            return entryPrice * (1 + this.takeProfitPercent / 100);
        }
        return entryPrice * (1 - this.takeProfitPercent / 100);
    }
}

module.exports = RiskManager;
