/**
 * Trading Strategy Engine
 * =======================
 * RSI + MACD + EMA Crossover Strategy with ADX Trend Filter,
 * ATR Volatility, Bollinger Bands, and Volume Confirmation
 */

const { RSI, MACD, EMA, SMA, ADX, ATR, BollingerBands } = require('technicalindicators');

class Strategy {
    constructor(config) {
        this.config = config;
        this.rsiPeriod = config.rsiPeriod || 14;
        this.rsiOversold = config.rsiOversold || 35;
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
     * Calculate ADX (Average Directional Index) for trend strength
     * ADX >= 20 = trending, ADX < 20 = ranging/choppy
     */
    calculateADX(highs, lows, closes, period = 14) {
        const values = ADX.calculate({
            high: highs,
            low: lows,
            close: closes,
            period: period
        });
        return values;
    }

    /**
     * Calculate ATR (Average True Range) for volatility measurement
     */
    calculateATR(highs, lows, closes, period = 14) {
        const values = ATR.calculate({
            high: highs,
            low: lows,
            close: closes,
            period: period
        });
        return values;
    }

    /**
     * Calculate Bollinger Bands for mean-reversion context
     */
    calculateBB(closes, period = 20, stdDev = 2) {
        const values = BollingerBands.calculate({
            values: closes,
            period: period,
            stdDev: stdDev
        });
        return values;
    }

    /**
     * Detect real RSI divergence using swing highs/lows over lookback period
     */
    detectRSIDivergence(closes, rsiValues, lookback = 15) {
        const result = { bullish: false, bearish: false };
        if (closes.length < lookback + 2 || rsiValues.length < lookback + 2) return result;

        const len = closes.length;
        const rsiLen = rsiValues.length;
        const offset = len - rsiLen;

        const priceLows = [];
        const priceHighs = [];
        for (let i = len - lookback; i < len - 1; i++) {
            if (i < 1) continue;
            const rsiIdx = i - offset;
            if (rsiIdx < 1 || rsiIdx >= rsiLen - 1) continue;

            if (closes[i] < closes[i - 1] && closes[i] < closes[i + 1]) {
                priceLows.push({ price: closes[i], rsi: rsiValues[rsiIdx], idx: i });
            }
            if (closes[i] > closes[i - 1] && closes[i] > closes[i + 1]) {
                priceHighs.push({ price: closes[i], rsi: rsiValues[rsiIdx], idx: i });
            }
        }

        if (priceLows.length >= 2) {
            const prev = priceLows[priceLows.length - 2];
            const last = priceLows[priceLows.length - 1];
            if (last.price < prev.price && last.rsi > prev.rsi && last.rsi < 50) {
                result.bullish = true;
            }
        }

        if (priceHighs.length >= 2) {
            const prev = priceHighs[priceHighs.length - 2];
            const last = priceHighs[priceHighs.length - 1];
            if (last.price > prev.price && last.rsi < prev.rsi && last.rsi > 50) {
                result.bearish = true;
            }
        }

        return result;
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

        // ADX trend detection
        const adxValues = this.calculateADX(highs, lows, closes, 14);
        const currentADX = adxValues.length > 0 ? adxValues[adxValues.length - 1] : null;
        const adxStrength = currentADX ? currentADX.adx : 0;
        const isTrending = adxStrength >= 20;
        const isUptrend = currentADX ? currentADX.pdi > currentADX.mdi : false;
        const marketRegime = isTrending ? 'TRENDING' : 'RANGING';

        // ATR volatility
        const atrValues = this.calculateATR(highs, lows, closes, 14);
        const currentATR = atrValues.length > 0 ? atrValues[atrValues.length - 1] : 0;
        const atrPercent = currentPrice > 0 ? (currentATR / currentPrice) * 100 : 0;

        // Bollinger Bands
        const bbValues = this.calculateBB(closes, 20, 2);
        const currentBB = bbValues.length > 0 ? bbValues[bbValues.length - 1] : null;
        let pbValue = 0.5;
        let nearLowerBand = false;
        let nearUpperBand = false;
        if (currentBB && currentBB.upper !== currentBB.lower) {
            pbValue = (currentPrice - currentBB.lower) / (currentBB.upper - currentBB.lower);
            nearLowerBand = pbValue < 0.1;
            nearUpperBand = pbValue > 0.9;
        }

        // RSI divergence (proper swing-based detection)
        const divergence = this.detectRSIDivergence(closes, rsiValues, 15);
        
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
        
        // 2. RSI Divergence (proper swing-based)
        if (divergence.bullish) {
            buySignals += 2;
            reasons.push('Bullish RSI divergence (swing)');
        }
        
        // 3. MACD Crossover (bullish) — ONLY in trending markets
        if (currentMACD && prevMACD && isTrending) {
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
        
        // 4. EMA Crossover (bullish) — ONLY in trending markets
        if (isTrending) {
            if (currentEMAFast > currentEMASlow && prevEMAFast <= prevEMASlow) {
                buySignals += 2;
                reasons.push('EMA bullish crossover');
            } else if (currentEMAFast > currentEMASlow) {
                buySignals += 1;
                reasons.push('Price above EMA trend');
            }
        }

        // 4b. ADX direction alignment bonus
        if (isTrending && isUptrend && buySignals > 0) {
            buySignals += 1;
            reasons.push(`ADX uptrend (${adxStrength.toFixed(0)})`);
        }
        
        // 5. Volume Confirmation
        if (currentVolume > avgVolume * 1.5) {
            buySignals += 1;
            reasons.push('High volume confirmation');
        }
        
        // 6. Price above EMAs (trend confirmation) — only when trending
        if (isTrending && currentPrice > currentEMAFast && currentPrice > currentEMASlow) {
            buySignals += 1;
            reasons.push('Price in uptrend');
        }

        // 7. Bollinger Band bounce — RANGING market mean-reversion
        if (!isTrending && nearLowerBand && currentRSI < 45) {
            buySignals += 2;
            reasons.push(`BB lower band bounce (pb=${pbValue.toFixed(2)}, ranging)`);
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
        
        // 2. RSI Divergence (proper swing-based)
        if (divergence.bearish) {
            sellSignals += 2;
            reasons.push('Bearish RSI divergence (swing)');
        }
        
        // 3. MACD Crossover (bearish) — ONLY in trending markets
        if (currentMACD && prevMACD && isTrending) {
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
        
        // 4. EMA Crossover (bearish) — ONLY in trending markets
        if (isTrending) {
            if (currentEMAFast < currentEMASlow && prevEMAFast >= prevEMASlow) {
                sellSignals += 2;
                reasons.push('EMA bearish crossover');
            } else if (currentEMAFast < currentEMASlow) {
                sellSignals += 1;
                reasons.push('Price below EMA trend');
            }
        }

        // 4b. ADX direction alignment bonus (downtrend)
        if (isTrending && !isUptrend && sellSignals > 0) {
            sellSignals += 1;
            reasons.push(`ADX downtrend (${adxStrength.toFixed(0)})`);
        }
        
        // 5. Price below EMAs (downtrend) — only when trending
        if (isTrending && currentPrice < currentEMAFast && currentPrice < currentEMASlow) {
            sellSignals += 1;
            reasons.push('Price in downtrend');
        }

        // 6. Bollinger Band upper rejection — mean-reversion sell
        if (nearUpperBand && currentRSI > 55) {
            sellSignals += 2;
            reasons.push(`BB upper band rejection (pb=${pbValue.toFixed(2)})`);
        }
        
        // ========== DETERMINE ACTION ==========
        
        const netSignal = buySignals - sellSignals;
        let action = 'HOLD';
        let strength = 0;
        
        // Calculate strength based on total signals (show even for HOLD)
        // This lets us see how close we are to BUY/SELL threshold
        const totalSignals = Math.max(buySignals, sellSignals);
        const baseStrength = Math.min(totalSignals / 6, 1); // 0-1 based on signal count
        
        // Require minimum signal strength to act
        if (netSignal >= 3) {
            action = 'BUY';
            strength = Math.min(netSignal / 6, 1); // Normalize to 0-1
        } else if (netSignal <= -3) {
            action = 'SELL';
            strength = Math.min(Math.abs(netSignal) / 6, 1);
        } else {
            // HOLD - but show how strong the potential signal is
            strength = baseStrength * 0.5; // Scale down for HOLD (max 0.5)
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
                price: currentPrice,
                adx: adxStrength,
                marketRegime,
                atr: currentATR,
                atrPercent,
                bb: currentBB,
                bbMiddle: currentBB ? currentBB.middle : null,
                pbValue
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
