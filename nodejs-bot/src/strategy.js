/**
 * Trading Strategy Engine
 * =======================
 * RSI + MACD + EMA Crossover Strategy with Volume Confirmation
 */

const { RSI, MACD, EMA, SMA } = require('technicalindicators');

class Strategy {
    constructor(config) {
        this.config = config;
        this.rsiPeriod = config.rsiPeriod || 14;
        this.rsiOversold = config.rsiOversold || 30;
        this.rsiOverbought = config.rsiOverbought || 70;
        this.emaFast = config.emaFast || 9;
        this.emaSlow = config.emaSlow || 21;
    }
    
    /**
     * Calculate RSI indicator
     */
    calculateRSI(closes) {
        const values = RSI.calculate({
            values: closes,
            period: this.rsiPeriod
        });
        return values;
    }
    
    /**
     * Calculate MACD indicator
     */
    calculateMACD(closes) {
        const values = MACD.calculate({
            values: closes,
            fastPeriod: 12,
            slowPeriod: 26,
            signalPeriod: 9,
            SimpleMAOscillator: false,
            SimpleMASignal: false
        });
        return values;
    }
    
    /**
     * Calculate EMA
     */
    calculateEMA(closes, period) {
        const values = EMA.calculate({
            values: closes,
            period: period
        });
        return values;
    }
    
    /**
     * Calculate Volume SMA
     */
    calculateVolumeSMA(volumes, period = 20) {
        const values = SMA.calculate({
            values: volumes,
            period: period
        });
        return values;
    }
    
    /**
     * Analyze candles and generate trading signal
     * @param {Array} candles - OHLCV candle data
     * @returns {Object} Signal with action, strength, and reasons
     */
    analyze(candles) {
        if (!candles || candles.length < 50) {
            return { action: 'HOLD', strength: 0, reasons: ['Insufficient data'] };
        }
        
        const closes = candles.map(c => c.close);
        const volumes = candles.map(c => c.volume);
        const highs = candles.map(c => c.high);
        const lows = candles.map(c => c.low);
        
        // Calculate indicators
        const rsiValues = this.calculateRSI(closes);
        const macdValues = this.calculateMACD(closes);
        const emaFastValues = this.calculateEMA(closes, this.emaFast);
        const emaSlowValues = this.calculateEMA(closes, this.emaSlow);
        const volumeSMA = this.calculateVolumeSMA(volumes, 20);
        
        // Get latest values
        const currentRSI = rsiValues[rsiValues.length - 1];
        const prevRSI = rsiValues[rsiValues.length - 2];
        
        const currentMACD = macdValues[macdValues.length - 1];
        const prevMACD = macdValues[macdValues.length - 2];
        
        const currentEMAFast = emaFastValues[emaFastValues.length - 1];
        const prevEMAFast = emaFastValues[emaFastValues.length - 2];
        const currentEMASlow = emaSlowValues[emaSlowValues.length - 1];
        const prevEMASlow = emaSlowValues[emaSlowValues.length - 2];
        
        const currentVolume = volumes[volumes.length - 1];
        const avgVolume = volumeSMA[volumeSMA.length - 1];
        
        const currentPrice = closes[closes.length - 1];
        
        // Initialize signal tracking
        let buySignals = 0;
        let sellSignals = 0;
        const reasons = [];
        
        // ========== BUY SIGNALS ==========
        
        // 1. RSI Oversold
        if (currentRSI < this.rsiOversold) {
            buySignals += 2;
            reasons.push(`RSI oversold (${currentRSI.toFixed(1)})`);
        } else if (currentRSI < 40) {
            buySignals += 1;
            reasons.push(`RSI low (${currentRSI.toFixed(1)})`);
        }
        
        // 2. RSI Divergence (price making lower lows but RSI making higher lows)
        if (prevRSI && currentRSI > prevRSI && closes[closes.length - 1] < closes[closes.length - 2]) {
            buySignals += 1;
            reasons.push('Bullish RSI divergence');
        }
        
        // 3. MACD Crossover (bullish)
        if (currentMACD && prevMACD) {
            if (currentMACD.MACD > currentMACD.signal && prevMACD.MACD <= prevMACD.signal) {
                buySignals += 2;
                reasons.push('MACD bullish crossover');
            } else if (currentMACD.MACD > currentMACD.signal) {
                buySignals += 1;
                reasons.push('MACD bullish');
            }
            
            // MACD Histogram increasing
            if (currentMACD.histogram > prevMACD.histogram && currentMACD.histogram > 0) {
                buySignals += 1;
                reasons.push('MACD momentum increasing');
            }
        }
        
        // 4. EMA Crossover (bullish)
        if (currentEMAFast > currentEMASlow && prevEMAFast <= prevEMASlow) {
            buySignals += 2;
            reasons.push('EMA bullish crossover');
        } else if (currentEMAFast > currentEMASlow) {
            buySignals += 1;
            reasons.push('Price above EMA trend');
        }
        
        // 5. Volume Confirmation
        if (currentVolume > avgVolume * 1.5) {
            buySignals += 1;
            reasons.push('High volume confirmation');
        }
        
        // 6. Price above EMAs (trend confirmation)
        if (currentPrice > currentEMAFast && currentPrice > currentEMASlow) {
            buySignals += 1;
            reasons.push('Price in uptrend');
        }
        
        // ========== SELL SIGNALS ==========
        
        // 1. RSI Overbought
        if (currentRSI > this.rsiOverbought) {
            sellSignals += 2;
            reasons.push(`RSI overbought (${currentRSI.toFixed(1)})`);
        } else if (currentRSI > 60) {
            sellSignals += 1;
            reasons.push(`RSI elevated (${currentRSI.toFixed(1)})`);
        }
        
        // 2. RSI Divergence (bearish)
        if (prevRSI && currentRSI < prevRSI && closes[closes.length - 1] > closes[closes.length - 2]) {
            sellSignals += 1;
            reasons.push('Bearish RSI divergence');
        }
        
        // 3. MACD Crossover (bearish)
        if (currentMACD && prevMACD) {
            if (currentMACD.MACD < currentMACD.signal && prevMACD.MACD >= prevMACD.signal) {
                sellSignals += 2;
                reasons.push('MACD bearish crossover');
            } else if (currentMACD.MACD < currentMACD.signal) {
                sellSignals += 1;
                reasons.push('MACD bearish');
            }
            
            // MACD Histogram decreasing
            if (currentMACD.histogram < prevMACD.histogram && currentMACD.histogram < 0) {
                sellSignals += 1;
                reasons.push('MACD momentum decreasing');
            }
        }
        
        // 4. EMA Crossover (bearish)
        if (currentEMAFast < currentEMASlow && prevEMAFast >= prevEMASlow) {
            sellSignals += 2;
            reasons.push('EMA bearish crossover');
        } else if (currentEMAFast < currentEMASlow) {
            sellSignals += 1;
            reasons.push('Price below EMA trend');
        }
        
        // 5. Price below EMAs (downtrend)
        if (currentPrice < currentEMAFast && currentPrice < currentEMASlow) {
            sellSignals += 1;
            reasons.push('Price in downtrend');
        }
        
        // ========== DETERMINE ACTION ==========
        
        const netSignal = buySignals - sellSignals;
        let action = 'HOLD';
        let strength = 0;
        
        // Require minimum signal strength to act
        if (netSignal >= 3) {
            action = 'BUY';
            strength = Math.min(netSignal / 6, 1); // Normalize to 0-1
        } else if (netSignal <= -3) {
            action = 'SELL';
            strength = Math.min(Math.abs(netSignal) / 6, 1);
        }
        
        return {
            action,
            strength,
            buySignals,
            sellSignals,
            netSignal,
            reasons,
            indicators: {
                rsi: currentRSI,
                macd: currentMACD,
                emaFast: currentEMAFast,
                emaSlow: currentEMASlow,
                volume: currentVolume,
                avgVolume,
                price: currentPrice
            }
        };
    }
    
    /**
     * Check if a position should be closed based on stop-loss or take-profit
     */
    checkExitConditions(position, currentPrice, stopLossPercent, takeProfitPercent) {
        const pnlPercent = ((currentPrice - position.entryPrice) / position.entryPrice) * 100;
        
        // Take profit
        if (pnlPercent >= takeProfitPercent) {
            return {
                shouldExit: true,
                reason: 'TAKE_PROFIT',
                pnlPercent
            };
        }
        
        // Stop loss
        if (pnlPercent <= -stopLossPercent) {
            return {
                shouldExit: true,
                reason: 'STOP_LOSS',
                pnlPercent
            };
        }
        
        return {
            shouldExit: false,
            pnlPercent
        };
    }
}

module.exports = Strategy;
