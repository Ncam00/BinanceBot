/**
 * DCA (Dollar Cost Averaging) Manager
 * ====================================
 * When a position goes against you, buy more at lower price to average down.
 * This turns potential losers into winners faster!
 */

const chalk = require('chalk');

class DCAManager {
    constructor(config) {
        // DCA Settings
        this.enabled = config.dcaEnabled !== false; // ON by default
        this.maxDCAOrders = config.dcaMaxOrders || 2; // Max 2 extra buys per position
        this.dcaDropPercent = config.dcaDropPercent || 2.5; // Buy more when down 2.5%
        this.dcaMultiplier = config.dcaMultiplier || 1.5; // Each DCA order is 1.5x previous
        
        // Track DCA orders per position
        this.dcaOrders = new Map(); // symbol -> { count, totalInvested, avgPrice }
        
        console.log(chalk.cyan('  📊 DCA Manager: ENABLED'));
        console.log(chalk.gray(`     Max DCA orders: ${this.maxDCAOrders}`));
        console.log(chalk.gray(`     DCA trigger: -${this.dcaDropPercent}%`));
        console.log(chalk.gray(`     DCA multiplier: ${this.dcaMultiplier}x`));
    }

    /**
     * Initialize DCA tracking for a new position
     */
    initPosition(symbol, amount, price) {
        this.dcaOrders.set(symbol, {
            count: 0,
            orders: [{
                amount,
                price,
                timestamp: Date.now()
            }],
            totalAmount: amount,
            totalInvested: amount * price,
            avgPrice: price
        });
    }

    /**
     * Check if position qualifies for DCA (averaging down)
     * Returns { shouldDCA: boolean, suggestedAmount: number, reason: string }
     */
    checkDCA(position, currentPrice, availableBalance) {
        if (!this.enabled) {
            return { shouldDCA: false, reason: 'DCA disabled' };
        }

        const symbol = position.symbol;
        let dcaData = this.dcaOrders.get(symbol);

        // Initialize if not tracked
        if (!dcaData) {
            this.initPosition(symbol, position.amount, position.entryPrice);
            dcaData = this.dcaOrders.get(symbol);
        }

        // Check if max DCA orders reached
        if (dcaData.count >= this.maxDCAOrders) {
            return { 
                shouldDCA: false, 
                reason: `Max DCA orders reached (${this.maxDCAOrders})` 
            };
        }

        // Calculate drop percentage from average price
        const dropPercent = ((dcaData.avgPrice - currentPrice) / dcaData.avgPrice) * 100;

        // Check if price dropped enough for DCA
        const dcaTrigger = this.dcaDropPercent * (dcaData.count + 1); // Increase threshold for each DCA
        
        if (dropPercent < dcaTrigger) {
            return { 
                shouldDCA: false, 
                reason: `Drop ${dropPercent.toFixed(2)}% < trigger ${dcaTrigger.toFixed(2)}%` 
            };
        }

        // Calculate DCA order size (multiplier of original position)
        const originalOrderValue = dcaData.orders[0].amount * dcaData.orders[0].price;
        const dcaOrderValue = originalOrderValue * Math.pow(this.dcaMultiplier, dcaData.count + 1);

        // Check if we have enough balance
        if (availableBalance < dcaOrderValue) {
            return { 
                shouldDCA: false, 
                reason: `Insufficient balance ($${availableBalance.toFixed(2)} < $${dcaOrderValue.toFixed(2)})` 
            };
        }

        // Calculate suggested amount
        const suggestedAmount = dcaOrderValue / currentPrice;

        return {
            shouldDCA: true,
            suggestedAmount,
            suggestedValue: dcaOrderValue,
            currentDropPercent: dropPercent,
            dcaNumber: dcaData.count + 1,
            reason: `Price dropped ${dropPercent.toFixed(2)}% - DCA #${dcaData.count + 1} triggered`
        };
    }

    /**
     * Record a DCA order execution
     * Returns new average price
     */
    recordDCAOrder(symbol, amount, price) {
        let dcaData = this.dcaOrders.get(symbol);
        
        if (!dcaData) {
            this.initPosition(symbol, amount, price);
            return price;
        }

        // Add new order
        dcaData.orders.push({
            amount,
            price,
            timestamp: Date.now()
        });
        dcaData.count++;
        dcaData.totalAmount += amount;
        dcaData.totalInvested += amount * price;
        
        // Calculate new average price
        dcaData.avgPrice = dcaData.totalInvested / dcaData.totalAmount;

        this.dcaOrders.set(symbol, dcaData);

        console.log(chalk.cyan('\n═══════════════════════════════════════════════════════════'));
        console.log(chalk.cyan('  📊 DCA ORDER RECORDED'));
        console.log(chalk.cyan('═══════════════════════════════════════════════════════════'));
        console.log(chalk.white(`  Symbol:            ${symbol}`));
        console.log(chalk.white(`  DCA Order #:       ${dcaData.count}`));
        console.log(chalk.white(`  Amount Added:      ${amount.toFixed(8)}`));
        console.log(chalk.white(`  Buy Price:         $${price.toFixed(4)}`));
        console.log(chalk.green(`  Total Amount:      ${dcaData.totalAmount.toFixed(8)}`));
        console.log(chalk.green(`  New Avg Price:     $${dcaData.avgPrice.toFixed(4)}`));
        console.log(chalk.yellow(`  Total Invested:    $${dcaData.totalInvested.toFixed(2)}`));
        console.log(chalk.cyan('═══════════════════════════════════════════════════════════\n'));

        return dcaData.avgPrice;
    }

    /**
     * Get DCA data for a position
     */
    getDCAData(symbol) {
        return this.dcaOrders.get(symbol) || null;
    }

    /**
     * Clear DCA tracking when position is closed
     */
    clearPosition(symbol) {
        this.dcaOrders.delete(symbol);
    }

    /**
     * Calculate breakeven price after DCA
     */
    getBreakevenPrice(symbol) {
        const dcaData = this.dcaOrders.get(symbol);
        if (!dcaData) return null;
        
        return dcaData.avgPrice;
    }

    /**
     * Calculate profit/loss based on DCA average
     */
    calculatePnL(symbol, currentPrice) {
        const dcaData = this.dcaOrders.get(symbol);
        if (!dcaData) return null;

        const currentValue = dcaData.totalAmount * currentPrice;
        const pnl = currentValue - dcaData.totalInvested;
        const pnlPercent = (pnl / dcaData.totalInvested) * 100;

        return {
            pnl,
            pnlPercent,
            avgPrice: dcaData.avgPrice,
            totalAmount: dcaData.totalAmount,
            totalInvested: dcaData.totalInvested,
            currentValue,
            dcaCount: dcaData.count
        };
    }
}

module.exports = DCAManager;
