/**
 * Advanced Analysis Module
 * Adds: Order Book Analysis, Multi-Timeframe, Support/Resistance
 * NEW: Volume Confirmation, BTC Correlation, Bollinger Bands
 */

class AdvancedAnalysis {
    constructor(exchange) {
        this.exchange = exchange;
        this.supportResistanceLevels = new Map(); // symbol -> { supports: [], resistances: [] }
        this.priceHistory = new Map(); // symbol -> price array for S/R detection
        this.btcTrendCache = { trend: 'neutral', timestamp: 0 }; // Cache BTC trend
        this.volumeCache = new Map(); // symbol -> { avgVolume, timestamp }
    }

    /**
     * 1. ORDER BOOK ANALYSIS
     * Detect buy/sell walls that could block price movement
     */
    async analyzeOrderBook(symbol) {
        try {
            const orderBook = await this.exchange.fetchOrderBook(symbol, 20);
            
            if (!orderBook || !orderBook.bids || !orderBook.asks) {
                return { signal: 'neutral', strength: 0, reason: 'No order book data' };
            }

            // Calculate total bid/ask volume
            const bidVolume = orderBook.bids.reduce((sum, [price, amount]) => sum + (price * amount), 0);
            const askVolume = orderBook.asks.reduce((sum, [price, amount]) => sum + (price * amount), 0);
            
            // Detect walls (large orders > 3x average)
            const avgBidSize = bidVolume / orderBook.bids.length;
            const avgAskSize = askVolume / orderBook.asks.length;
            
            const bidWalls = orderBook.bids.filter(([price, amount]) => (price * amount) > avgBidSize * 3);
            const askWalls = orderBook.asks.filter(([price, amount]) => (price * amount) > avgAskSize * 3);
            
            // Calculate imbalance ratio
            const imbalanceRatio = bidVolume / (bidVolume + askVolume);
            
            // Determine signal
            let signal = 'neutral';
            let strength = 0;
            let reason = '';
            
            if (imbalanceRatio > 0.6) {
                signal = 'bullish';
                strength = Math.min((imbalanceRatio - 0.5) * 2, 1);
                reason = `Strong buy pressure (${(imbalanceRatio * 100).toFixed(1)}% bids)`;
            } else if (imbalanceRatio < 0.4) {
                signal = 'bearish';
                strength = Math.min((0.5 - imbalanceRatio) * 2, 1);
                reason = `Strong sell pressure (${((1 - imbalanceRatio) * 100).toFixed(1)}% asks)`;
            }
            
            // Check for walls near current price (only very close massive walls matter)
            const currentPrice = (orderBook.bids[0][0] + orderBook.asks[0][0]) / 2;
            const nearbyAskWall = askWalls.find(([price, amount]) => price < currentPrice * 1.005 && (price * amount) > avgAskSize * 5);
            const nearbyBidWall = bidWalls.find(([price, amount]) => price > currentPrice * 0.995 && (price * amount) > avgBidSize * 5);
            
            return {
                signal,
                strength,
                reason,
                imbalanceRatio,
                hasNearbyResistance: !!nearbyAskWall,
                hasNearbySupport: !!nearbyBidWall,
                bidVolume,
                askVolume,
                wallWarning: nearbyAskWall ? `Sell wall at ${nearbyAskWall[0].toFixed(2)}` : null
            };
        } catch (error) {
            console.error(`Order book analysis failed for ${symbol}:`, error.message);
            return { signal: 'neutral', strength: 0, reason: 'Error fetching order book' };
        }
    }

    /**
     * 2. MULTI-TIMEFRAME ANALYSIS
     * Check 1m, 5m, 15m for trend confirmation
     */
    async multiTimeframeAnalysis(symbol, strategy) {
        try {
            const timeframes = ['1m', '5m', '15m'];
            const signals = {};
            
            for (const tf of timeframes) {
                const candles = await this.exchange.fetchOHLCV(symbol, tf, undefined, 50);
                if (!candles || candles.length < 20) continue;
                
                // Calculate simple trend for each timeframe
                const closes = candles.map(c => c[4]);
                const sma10 = closes.slice(-10).reduce((a, b) => a + b, 0) / 10;
                const sma20 = closes.slice(-20).reduce((a, b) => a + b, 0) / 20;
                const currentPrice = closes[closes.length - 1];
                
                // RSI calculation
                const rsi = this.calculateRSI(closes, 14);
                
                signals[tf] = {
                    trend: currentPrice > sma10 && sma10 > sma20 ? 'bullish' : 
                           currentPrice < sma10 && sma10 < sma20 ? 'bearish' : 'neutral',
                    rsi,
                    priceVsSMA: ((currentPrice - sma20) / sma20) * 100
                };
            }
            
            // Count aligned signals
            const bullishCount = Object.values(signals).filter(s => s.trend === 'bullish').length;
            const bearishCount = Object.values(signals).filter(s => s.trend === 'bearish').length;
            
            let overallSignal = 'neutral';
            let confidence = 0;
            
            if (bullishCount >= 2) {
                overallSignal = 'bullish';
                confidence = bullishCount / 3;
            } else if (bearishCount >= 2) {
                overallSignal = 'bearish';
                confidence = bearishCount / 3;
            }
            
            return {
                signal: overallSignal,
                confidence,
                timeframes: signals,
                aligned: bullishCount === 3 || bearishCount === 3,
                reason: `${bullishCount}/3 bullish, ${bearishCount}/3 bearish timeframes`
            };
        } catch (error) {
            console.error(`Multi-timeframe analysis failed for ${symbol}:`, error.message);
            return { signal: 'neutral', confidence: 0, reason: 'Error' };
        }
    }

    /**
     * 3. SUPPORT/RESISTANCE DETECTION
     * Find key price levels from recent highs/lows
     */
    async detectSupportResistance(symbol) {
        try {
            const candles = await this.exchange.fetchOHLCV(symbol, '15m', undefined, 100);
            if (!candles || candles.length < 50) {
                return { supports: [], resistances: [], nearSupport: false, nearResistance: false };
            }
            
            const highs = candles.map(c => c[2]);
            const lows = candles.map(c => c[3]);
            const currentPrice = candles[candles.length - 1][4];
            
            // Find pivot points (local highs and lows)
            const pivotHighs = [];
            const pivotLows = [];
            
            for (let i = 2; i < candles.length - 2; i++) {
                // Pivot high: higher than 2 candles on each side
                if (highs[i] > highs[i-1] && highs[i] > highs[i-2] && 
                    highs[i] > highs[i+1] && highs[i] > highs[i+2]) {
                    pivotHighs.push(highs[i]);
                }
                // Pivot low: lower than 2 candles on each side
                if (lows[i] < lows[i-1] && lows[i] < lows[i-2] && 
                    lows[i] < lows[i+1] && lows[i] < lows[i+2]) {
                    pivotLows.push(lows[i]);
                }
            }
            
            // Cluster nearby levels (within 0.5%)
            const clusterLevels = (levels) => {
                const clustered = [];
                const sorted = [...levels].sort((a, b) => a - b);
                
                for (const level of sorted) {
                    const nearby = clustered.find(c => Math.abs(c - level) / c < 0.005);
                    if (!nearby) {
                        clustered.push(level);
                    }
                }
                return clustered;
            };
            
            const resistances = clusterLevels(pivotHighs).filter(r => r > currentPrice).slice(0, 3);
            const supports = clusterLevels(pivotLows).filter(s => s < currentPrice).slice(-3);
            
            // Check if price is near support or resistance
            const nearResistance = resistances.some(r => (r - currentPrice) / currentPrice < 0.01);
            const nearSupport = supports.some(s => (currentPrice - s) / currentPrice < 0.01);
            
            // Calculate distance to nearest levels
            const nearestResistance = resistances[0] || null;
            const nearestSupport = supports[supports.length - 1] || null;
            
            this.supportResistanceLevels.set(symbol, { supports, resistances });
            
            return {
                supports,
                resistances,
                nearSupport,
                nearResistance,
                nearestResistance,
                nearestSupport,
                currentPrice,
                resistanceDistance: nearestResistance ? ((nearestResistance - currentPrice) / currentPrice * 100).toFixed(2) + '%' : null,
                supportDistance: nearestSupport ? ((currentPrice - nearestSupport) / currentPrice * 100).toFixed(2) + '%' : null
            };
        } catch (error) {
            console.error(`S/R detection failed for ${symbol}:`, error.message);
            return { supports: [], resistances: [], nearSupport: false, nearResistance: false };
        }
    }

    /**
     * 4. VOLUME CONFIRMATION (NEW!)
     * Only buy when volume is above average - avoids fake breakouts
     */
    async analyzeVolume(symbol) {
        try {
            const candles = await this.exchange.fetchOHLCV(symbol, '5m', undefined, 50);
            if (!candles || candles.length < 20) {
                return { confirmed: true, ratio: 1, reason: 'Insufficient data' };
            }

            const volumes = candles.map(c => c[5]);
            const currentVolume = volumes[volumes.length - 1];
            const avgVolume = volumes.slice(0, -1).reduce((a, b) => a + b, 0) / (volumes.length - 1);
            
            const volumeRatio = currentVolume / avgVolume;
            
            // Volume should be at least 80% of average for buys
            const confirmed = volumeRatio >= 0.8;
            
            // High volume (>1.5x) is very bullish
            const highVolume = volumeRatio >= 1.5;
            
            let reason = '';
            if (volumeRatio < 0.5) reason = '⚠️ Very low volume (fake move?)';
            else if (volumeRatio < 0.8) reason = 'Low volume - weak signal';
            else if (volumeRatio > 2) reason = '🔥 Volume spike - strong move!';
            else if (volumeRatio > 1.5) reason = 'High volume - confirmed';
            else reason = 'Normal volume';

            return {
                confirmed,
                highVolume,
                ratio: volumeRatio,
                currentVolume,
                avgVolume,
                reason
            };
        } catch (error) {
            console.error(`Volume analysis failed for ${symbol}:`, error.message);
            return { confirmed: true, ratio: 1, reason: 'Error' };
        }
    }

    /**
     * 5. BTC CORRELATION FILTER (NEW!)
     * Don't buy alts when BTC is dumping - they usually follow
     */
    async analyzeBTCTrend() {
        try {
            // Cache for 30 seconds to reduce API calls
            const now = Date.now();
            if (this.btcTrendCache.timestamp > now - 30000) {
                return this.btcTrendCache;
            }

            const candles = await this.exchange.fetchOHLCV('BTCUSDT', '5m', undefined, 20);
            if (!candles || candles.length < 15) {
                return { trend: 'neutral', safe: true, reason: 'Insufficient BTC data' };
            }

            const closes = candles.map(c => c[4]);
            const currentPrice = closes[closes.length - 1];
            const price5mAgo = closes[closes.length - 2];
            const price15mAgo = closes[closes.length - 4];
            const price1hAgo = closes[0];

            // Calculate short-term momentum
            const change5m = ((currentPrice - price5mAgo) / price5mAgo) * 100;
            const change15m = ((currentPrice - price15mAgo) / price15mAgo) * 100;
            const change1h = ((currentPrice - price1hAgo) / price1hAgo) * 100;

            // Calculate EMA trend
            const ema10 = this.calculateEMA(closes, 10);
            const ema20 = this.calculateEMA(closes, 20);
            const emaTrend = ema10 > ema20 ? 'bullish' : 'bearish';

            let trend = 'neutral';
            let safe = true;
            let reason = '';

            // BTC dumping hard - don't buy alts!
            if (change15m < -0.5 || change1h < -1.5) {
                trend = 'bearish';
                safe = false;
                reason = `🚨 BTC dumping (${change1h.toFixed(2)}% 1h) - AVOID alts!`;
            }
            // BTC pumping - good for alts
            else if (change15m > 0.3 && change1h > 0.5) {
                trend = 'bullish';
                safe = true;
                reason = `✅ BTC bullish (${change1h.toFixed(2)}% 1h) - alts safe`;
            }
            // BTC stable
            else {
                trend = emaTrend;
                safe = true;
                reason = `BTC stable (${change1h.toFixed(2)}% 1h)`;
            }

            const result = {
                trend,
                safe,
                reason,
                change5m,
                change15m,
                change1h,
                emaTrend,
                timestamp: now
            };

            this.btcTrendCache = result;
            return result;
        } catch (error) {
            console.error('BTC trend analysis failed:', error.message);
            return { trend: 'neutral', safe: true, reason: 'Error checking BTC' };
        }
    }

    /**
     * 6. BOLLINGER BANDS (NEW!)
     * Buy at lower band, sell at upper band - great for ranging markets
     */
    async analyzeBollingerBands(symbol) {
        try {
            const candles = await this.exchange.fetchOHLCV(symbol, '5m', undefined, 30);
            if (!candles || candles.length < 20) {
                return { signal: 'neutral', position: 'middle', reason: 'Insufficient data' };
            }

            const closes = candles.map(c => c[4]);
            const currentPrice = closes[closes.length - 1];

            // Calculate 20-period SMA (middle band)
            const period = 20;
            const sma = closes.slice(-period).reduce((a, b) => a + b, 0) / period;

            // Calculate standard deviation
            const squaredDiffs = closes.slice(-period).map(c => Math.pow(c - sma, 2));
            const variance = squaredDiffs.reduce((a, b) => a + b, 0) / period;
            const stdDev = Math.sqrt(variance);

            // Bollinger Bands (2 standard deviations)
            const upperBand = sma + (stdDev * 2);
            const lowerBand = sma - (stdDev * 2);
            const bandwidth = ((upperBand - lowerBand) / sma) * 100;

            // Calculate position within bands (0 = lower band, 1 = upper band)
            const position = (currentPrice - lowerBand) / (upperBand - lowerBand);

            // Determine signal
            let signal = 'neutral';
            let strength = 0;
            let reason = '';

            if (position <= 0.15) {
                signal = 'buy';
                strength = 1 - position; // Stronger near bottom
                reason = `📉 At lower Bollinger Band (oversold) - BUY zone`;
            } else if (position >= 0.85) {
                signal = 'sell';
                strength = position;
                reason = `📈 At upper Bollinger Band (overbought) - SELL zone`;
            } else if (position < 0.35) {
                signal = 'buy';
                strength = 0.5;
                reason = 'Near lower band - potential entry';
            } else if (position > 0.65) {
                signal = 'sell';
                strength = 0.5;
                reason = 'Near upper band - consider exit';
            } else {
                signal = 'neutral';
                reason = 'Middle of Bollinger Bands';
            }

            // Squeeze detection (low bandwidth = breakout coming)
            const squeeze = bandwidth < 2;
            if (squeeze) {
                reason += ' ⚡ Squeeze detected - breakout imminent!';
            }

            return {
                signal,
                strength,
                position,
                reason,
                upperBand,
                lowerBand,
                middleBand: sma,
                bandwidth,
                squeeze,
                currentPrice
            };
        } catch (error) {
            console.error(`Bollinger Bands analysis failed for ${symbol}:`, error.message);
            return { signal: 'neutral', position: 0.5, reason: 'Error' };
        }
    }

    // Helper: Calculate EMA
    calculateEMA(data, period) {
        const k = 2 / (period + 1);
        let ema = data.slice(0, period).reduce((a, b) => a + b, 0) / period;
        
        for (let i = period; i < data.length; i++) {
            ema = data[i] * k + ema * (1 - k);
        }
        return ema;
    }

    /**
     * COMBINED ADVANCED SIGNAL
     * Merges ALL 6 analyses into one recommendation
     */
    async getAdvancedSignal(symbol, strategy) {
        // Run all analyses in parallel for speed
        const [orderBook, multiTF, supportResistance, volume, btcTrend, bollinger] = await Promise.all([
            this.analyzeOrderBook(symbol),
            this.multiTimeframeAnalysis(symbol, strategy),
            this.detectSupportResistance(symbol),
            this.analyzeVolume(symbol),
            this.analyzeBTCTrend(),
            this.analyzeBollingerBands(symbol)
        ]);

        // Score calculation
        let buyScore = 0;
        let sellScore = 0;
        let reasons = [];
        let blocked = false;
        let blockReason = '';

        // ========== BTC CORRELATION FILTER (CRITICAL!) ==========
        // If BTC is dumping, block ALL alt buys
        if (!btcTrend.safe && symbol !== 'BTCUSDT') {
            blocked = true;
            blockReason = btcTrend.reason;
            reasons.push(btcTrend.reason);
        }

        // ========== VOLUME CONFIRMATION ==========
        if (!volume.confirmed) {
            buyScore -= 0.25; // Penalize low volume buys
            reasons.push(volume.reason);
        }
        if (volume.highVolume) {
            buyScore += 0.1; // Bonus for high volume
            reasons.push(volume.reason);
        }

        // ========== BOLLINGER BANDS (weight: 25%) ==========
        if (bollinger.signal === 'buy') {
            buyScore += bollinger.strength * 0.25;
            reasons.push(bollinger.reason);
        } else if (bollinger.signal === 'sell') {
            sellScore += bollinger.strength * 0.25;
            reasons.push(bollinger.reason);
        }

        // ========== Order book contribution (weight: 20%) ==========
        if (orderBook.signal === 'bullish') {
            buyScore += orderBook.strength * 0.2;
            reasons.push(`OrderBook: ${orderBook.reason}`);
        } else if (orderBook.signal === 'bearish') {
            sellScore += orderBook.strength * 0.2;
            reasons.push(`OrderBook: ${orderBook.reason}`);
        }

        // Block buy if there's a nearby sell wall
        if (orderBook.hasNearbyResistance) {
            buyScore -= 0.15;
            reasons.push('⚠️ Sell wall detected nearby');
        }

        // ========== Multi-timeframe contribution (weight: 35%) ==========
        if (multiTF.signal === 'bullish') {
            buyScore += multiTF.confidence * 0.35;
            reasons.push(`MTF: ${multiTF.reason}`);
        } else if (multiTF.signal === 'bearish') {
            sellScore += multiTF.confidence * 0.35;
            reasons.push(`MTF: ${multiTF.reason}`);
        }

        // Bonus for aligned timeframes
        if (multiTF.aligned) {
            if (multiTF.signal === 'bullish') buyScore += 0.15;
            if (multiTF.signal === 'bearish') sellScore += 0.15;
            reasons.push('✓ All timeframes aligned');
        }

        // ========== Support/Resistance contribution (weight: 20%) ==========
        if (supportResistance.nearSupport) {
            buyScore += 0.15;
            reasons.push('Near support level (good entry)');
        }
        if (supportResistance.nearResistance) {
            sellScore += 0.15;
            reasons.push('Near resistance level (take profit zone)');
        }

        // Determine final signal
        let signal = 'HOLD';
        let strength = 0;
        
        // If blocked by BTC correlation, force HOLD for buys
        if (blocked && buyScore > sellScore) {
            signal = 'HOLD';
            strength = 0;
            reasons.unshift('🚫 BLOCKED: ' + blockReason);
        } else if (buyScore > sellScore && buyScore > 0.35) {
            signal = 'BUY';
            strength = Math.min(buyScore, 1);
        } else if (sellScore > buyScore && sellScore > 0.35) {
            signal = 'SELL';
            strength = Math.min(sellScore, 1);
        }

        return {
            symbol,
            signal,
            strength,
            buyScore,
            sellScore,
            blocked,
            blockReason,
            reasons,
            analysis: {
                orderBook: {
                    signal: orderBook.signal,
                    imbalance: orderBook.imbalanceRatio,
                    hasWall: orderBook.hasNearbyResistance
                },
                multiTimeframe: {
                    signal: multiTF.signal,
                    confidence: multiTF.confidence,
                    aligned: multiTF.aligned
                },
                supportResistance: {
                    nearSupport: supportResistance.nearSupport,
                    nearResistance: supportResistance.nearResistance,
                    levels: {
                        support: supportResistance.nearestSupport,
                        resistance: supportResistance.nearestResistance
                    }
                },
                volume: {
                    confirmed: volume.confirmed,
                    ratio: volume.ratio,
                    highVolume: volume.highVolume
                },
                btcTrend: {
                    trend: btcTrend.trend,
                    safe: btcTrend.safe,
                    change1h: btcTrend.change1h
                },
                bollinger: {
                    signal: bollinger.signal,
                    position: bollinger.position,
                    squeeze: bollinger.squeeze
                }
            }
        };
    }

    // Helper: Calculate RSI
    calculateRSI(closes, period = 14) {
        if (closes.length < period + 1) return 50;
        
        let gains = 0;
        let losses = 0;
        
        for (let i = closes.length - period; i < closes.length; i++) {
            const change = closes[i] - closes[i - 1];
            if (change > 0) gains += change;
            else losses -= change;
        }
        
        const avgGain = gains / period;
        const avgLoss = losses / period;
        
        if (avgLoss === 0) return 100;
        const rs = avgGain / avgLoss;
        return 100 - (100 / (1 + rs));
    }
}

module.exports = AdvancedAnalysis;
