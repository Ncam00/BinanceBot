/**
 * Database Manager
 * ================
 * JSON file-based storage for trade history and statistics
 */

const path = require('path');
const fs = require('fs');

class TradeDatabase {
    constructor(dbPath = null) {
        // Use data directory
        const dataDir = path.join(__dirname, '..', 'data');
        if (!fs.existsSync(dataDir)) {
            fs.mkdirSync(dataDir, { recursive: true });
        }
        
        this.tradesFile = path.join(dataDir, 'trades.json');
        this.statsFile = path.join(dataDir, 'stats.json');
        
        this.data = this.loadData();
    }
    
    /**
     * Load data from files
     */
    loadData() {
        let trades = [];
        let stats = { daily: {} };
        
        try {
            if (fs.existsSync(this.tradesFile)) {
                trades = JSON.parse(fs.readFileSync(this.tradesFile, 'utf8'));
            }
        } catch (e) {
            console.log('Creating new trades file');
        }
        
        try {
            if (fs.existsSync(this.statsFile)) {
                stats = JSON.parse(fs.readFileSync(this.statsFile, 'utf8'));
            }
        } catch (e) {
            console.log('Creating new stats file');
        }
        
        return { trades, stats };
    }
    
    /**
     * Save data to files
     */
    saveData() {
        try {
            fs.writeFileSync(this.tradesFile, JSON.stringify(this.data.trades, null, 2));
            fs.writeFileSync(this.statsFile, JSON.stringify(this.data.stats, null, 2));
        } catch (e) {
            console.error('Failed to save data:', e.message);
        }
    }
    
    /**
     * Log a trade
     */
    logTrade(trade) {
        const record = {
            id: this.data.trades.length + 1,
            timestamp: new Date().toISOString(),
            symbol: trade.symbol,
            side: trade.side,
            amount: trade.amount,
            price: trade.price,
            value: trade.value,
            orderId: trade.orderId || null,
            paper: trade.paper || false,
            signalStrength: trade.signalStrength || null,
            reasons: trade.reasons || null,
            pnl: trade.pnl || null,
            pnlPercent: trade.pnlPercent || null,
            closeReason: trade.closeReason || null
        };
        
        this.data.trades.push(record);
        this.saveData();
        return record;
    }
    
    /**
     * Get recent trades
     */
    getRecentTrades(limit = 50) {
        return this.data.trades
            .slice(-limit)
            .reverse();
    }
    
    /**
     * Get trades for a specific symbol
     */
    getTradesBySymbol(symbol, limit = 100) {
        return this.data.trades
            .filter(t => t.symbol === symbol)
            .slice(-limit)
            .reverse();
    }
    
    /**
     * Get today's trades
     */
    getTodayTrades() {
        // Use NZST timezone for "today" calculation
        const nzDate = new Date().toLocaleDateString('en-CA', { timeZone: 'Pacific/Auckland' }); // YYYY-MM-DD format
        return this.data.trades
            .filter(t => {
                const tradeDate = new Date(t.timestamp).toLocaleDateString('en-CA', { timeZone: 'Pacific/Auckland' });
                return tradeDate === nzDate;
            })
            .reverse();
    }
    
    /**
     * Get trading statistics
     */
    getStats() {
        const sellTrades = this.data.trades.filter(t => t.side === 'SELL');
        const todaySells = this.getTodayTrades().filter(t => t.side === 'SELL');
        
        const winningTrades = sellTrades.filter(t => t.pnl > 0).length;
        const losingTrades = sellTrades.filter(t => t.pnl < 0).length;
        const totalPnL = sellTrades.reduce((sum, t) => sum + (t.pnl || 0), 0);
        const todayPnL = todaySells.reduce((sum, t) => sum + (t.pnl || 0), 0);
        
        const pnlValues = sellTrades.map(t => t.pnl).filter(p => p !== null);
        
        return {
            totalTrades: sellTrades.length,
            winningTrades,
            losingTrades,
            winRate: sellTrades.length > 0 ? (winningTrades / sellTrades.length) * 100 : 0,
            totalPnL,
            avgProfit: pnlValues.length > 0 ? totalPnL / pnlValues.length : 0,
            maxWin: pnlValues.length > 0 ? Math.max(...pnlValues.filter(p => p > 0), 0) : 0,
            maxLoss: pnlValues.length > 0 ? Math.min(...pnlValues.filter(p => p < 0), 0) : 0,
            todayPnL,
            todayTrades: todaySells.length
        };
    }
    
    /**
     * Update daily stats
     */
    updateDailyStats(date, stats) {
        this.data.stats.daily[date] = stats;
        this.saveData();
    }
    
    /**
     * Get historical performance
     */
    getHistoricalPerformance(days = 30) {
        const dates = Object.keys(this.data.stats.daily).sort().reverse().slice(0, days);
        return dates.map(date => ({ date, ...this.data.stats.daily[date] }));
    }
    
    /**
     * Close database connection (no-op for JSON)
     */
    close() {
        this.saveData();
    }
}

module.exports = TradeDatabase;
