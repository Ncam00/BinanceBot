/**
 * Momentum Scalping Module
 * ========================
 * When market is hot (volume spike), switch to faster trades
 * Catches quick pumps with rapid entries/exits
 */

const chalk = require('chalk');

class MomentumScalper {
    constructor(exchange, config) {
        this.exchange = exchange;
        
        // Momentum settings
        this.enabled = config.momentumScalpingEnabled !== false;
        this.volumeSpikeThreshold = config.volumeSpikeThreshold || 2.0; // 2x average volume
        this.scalpTakeProfit = config.scalpTakeProfit || 0.8; // 0.8% quick profit
        this.scalpStopLoss = config.scalpStopLoss || 0.5; // 0.5% tight stop
        this.scalpHoldTime = config.scalpHoldTime || 5 * 60 * 1000; // 5 min max hold
        
        // Momentum tracking
        this.momentumActive = false;
        this.hotSymbols = new Map(); // symbol -> { volumeRatio, momentum, timestamp }
        this.lastScan = 0;
        this.scanInterval = 30 * 1000; // Scan every 30 seconds
        
        console.log(chalk.magenta('  ⚡ Momentum Scalper: ENABLED'));
        console.log(chalk.gray(`     Volume spike trigger: ${this.volumeSpikeThreshold}x`));
        console.log(chalk.gray(`     Scalp take profit: ${this.scalpTakeProfit}%`));
        console.log(chalk.gray(`     Scalp stop loss: ${this.scalpStopLoss}%`));
    }

    /**
     * Scan for momentum opportunities
     * Returns list of hot symbols to scalp
     */
    async scanForMomentum(symbols) {
        if (!this.enabled) return [];
        
        const now = Date.now();
        if (now - this.lastScan < this.scanInterval) {
            // Return cached hot symbols
            return Array.from(this.hotSymbols.entries())
                .filter(([_, data]) => now - data.timestamp < 60000)
                .map(([symbol, data]) => ({ symbol, ...data }));
        }
        
        this.lastScan = now;
        const hotList = [];

        for (const symbol of symbols) {
            try {
                const momentum = await this.analyzeMomentum(symbol);
                
                if (momentum.isHot) {
                    this.hotSymbols.set(symbol, {
                        ...momentum,
                        timestamp: now
                    });
                    hotList.push({ symbol, ...momentum });
                } else {
                    this.hotSymbols.delete(symbol);
                }
            } catch (error) {
                // Skip on error
            }
        }

        if (hotList.length > 0) {
            console.log(chalk.magenta(`\n  ⚡ MOMENTUM DETECTED: ${hotList.length} hot symbols`));
            hotList.forEach(h => {
                console.log(chalk.yellow(`     ${h.symbol}: Volume ${h.volumeRatio.toFixed(1)}x, Price ${h.priceChange > 0 ? '+' : ''}${h.priceChange.toFixed(2)}%`));
            });
        }

        this.momentumActive = hotList.length > 0;
        return hotList;
    }

    /**
     * Analyze momentum for a single symbol
     */
    async analyzeMomentum(symbol) {
        const candles = await this.exchange.fetchOHLCV(symbol, '1m', undefined, 30);
        
        if (!candles || candles.length < 20) {
            return { isHot: false, reason: 'Insufficient data' };
        }

        // Current candle
        const currentCandle = candles[candles.length - 1];
        const currentVolume = currentCandle[5];
        const currentClose = currentCandle[4];
        const currentOpen = currentCandle[1];

        // Average volume (last 20 candles, excluding current)
        const avgVolume = candles.slice(-21, -1).reduce((sum, c) => sum + c[5], 0) / 20;

        // Volume ratio
        const volumeRatio = currentVolume / avgVolume;

        // Price change in last 5 minutes
        const price5mAgo = candles[candles.length - 6]?.[4] || currentClose;
        const priceChange = ((currentClose - price5mAgo) / price5mAgo) * 100;

        // Price momentum (current candle)
        const candleMomentum = ((currentClose - currentOpen) / currentOpen) * 100;

        // Consecutive green/red candles
        let consecutiveGreen = 0;
        let consecutiveRed = 0;
        for (let i = candles.length - 1; i >= Math.max(0, candles.length - 5); i--) {
            if (candles[i][4] > candles[i][1]) {
                if (consecutiveRed === 0) consecutiveGreen++;
                else break;
            } else {
                if (consecutiveGreen === 0) consecutiveRed++;
                else break;
            }
        }

        // Determine if hot
        const isHot = (
            volumeRatio >= this.volumeSpikeThreshold && // Volume spike
            Math.abs(priceChange) >= 0.3 && // Price moving
            Math.abs(candleMomentum) >= 0.1 // Current candle has momentum
        );

        const direction = priceChange > 0 ? 'bullish' : 'bearish';

        return {
            isHot,
            volumeRatio,
            priceChange,
            candleMomentum,
            direction,
            consecutiveGreen,
            consecutiveRed,
            avgVolume,
            currentVolume,
            reason: isHot 
                ? `Volume ${volumeRatio.toFixed(1)}x, ${direction} momentum` 
                : 'No momentum'
        };
    }

    /**
     * Check if symbol is currently hot (for quick decisions)
     */
    isSymbolHot(symbol) {
        const data = this.hotSymbols.get(symbol);
        if (!data) return false;
        
        // Hot status expires after 60 seconds
        return Date.now() - data.timestamp < 60000;
    }

    /**
     * Get scalping parameters for a hot symbol
     * Returns tighter TP/SL for quick scalps
     */
    getScalpingParams(symbol) {
        const data = this.hotSymbols.get(symbol);
        
        if (!data || !data.isHot) {
            return null; // Not in scalping mode
        }

        // Adjust based on momentum strength
        const volumeStrength = Math.min(data.volumeRatio / this.volumeSpikeThreshold, 2);
        
        return {
            isScalp: true,
            takeProfit: this.scalpTakeProfit * volumeStrength, // 0.8% - 1.6%
            stopLoss: this.scalpStopLoss,
            maxHoldTime: this.scalpHoldTime,
            direction: data.direction,
            volumeRatio: data.volumeRatio,
            momentum: data.priceChange
        };
    }

    /**
     * Should we enter a scalp trade?
     */
    shouldEnterScalp(symbol, signal) {
        const momentum = this.hotSymbols.get(symbol);
        
        if (!momentum || !momentum.isHot) {
            return { enter: false, reason: 'No momentum' };
        }

        // Only scalp in direction of momentum
        if (momentum.direction === 'bullish' && signal.action === 'BUY') {
            return {
                enter: true,
                reason: `⚡ SCALP: Bullish momentum (Vol ${momentum.volumeRatio.toFixed(1)}x)`,
                params: this.getScalpingParams(symbol)
            };
        }

        if (momentum.direction === 'bearish' && signal.action === 'SELL') {
            return {
                enter: false, // We don't short in spot trading
                reason: 'Bearish momentum - skip (spot only)'
            };
        }

        return { enter: false, reason: 'Signal doesn\'t match momentum direction' };
    }

    /**
     * Check if scalp position should exit
     */
    shouldExitScalp(position, currentPrice) {
        const params = this.getScalpingParams(position.symbol);
        
        if (!params || !params.isScalp) {
            return { exit: false };
        }

        const pnlPercent = ((currentPrice - position.entryPrice) / position.entryPrice) * 100;
        const holdTime = Date.now() - position.timestamp;

        // Take profit hit
        if (pnlPercent >= params.takeProfit) {
            return {
                exit: true,
                reason: `⚡ SCALP TP: +${pnlPercent.toFixed(2)}% (target: ${params.takeProfit.toFixed(2)}%)`,
                pnlPercent
            };
        }

        // Stop loss hit
        if (pnlPercent <= -params.stopLoss) {
            return {
                exit: true,
                reason: `⚡ SCALP SL: ${pnlPercent.toFixed(2)}%`,
                pnlPercent
            };
        }

        // Max hold time exceeded with any profit
        if (holdTime >= params.maxHoldTime && pnlPercent > 0) {
            return {
                exit: true,
                reason: `⚡ SCALP TIME: +${pnlPercent.toFixed(2)}% after ${Math.round(holdTime / 60000)}min`,
                pnlPercent
            };
        }

        // Momentum fading - exit if in profit
        if (!this.isSymbolHot(position.symbol) && pnlPercent > 0.2) {
            return {
                exit: true,
                reason: `⚡ SCALP FADE: Momentum gone, taking +${pnlPercent.toFixed(2)}%`,
                pnlPercent
            };
        }

        return { exit: false };
    }

    /**
     * Get momentum status summary
     */
    getStatus() {
        return {
            enabled: this.enabled,
            momentumActive: this.momentumActive,
            hotSymbolCount: this.hotSymbols.size,
            hotSymbols: Array.from(this.hotSymbols.entries()).map(([symbol, data]) => ({
                symbol,
                volumeRatio: data.volumeRatio,
                direction: data.direction,
                priceChange: data.priceChange
            }))
        };
    }
}

module.exports = MomentumScalper;
