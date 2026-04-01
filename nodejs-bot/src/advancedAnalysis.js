/**
 * Advanced Analysis Module
 * Adds: Order Book Analysis, Multi-Timeframe, Support/Resistance
 */

class AdvancedAnalysis {
    constructor(exchange) {
        this.exchange = exchange;
        this.supportResistanceLevels = new Map(); // symbol -> { supports: [], resistances: [] }
        this.priceHistory = new Map(); // symbol -> price array for S/R detection
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
            
            // Check for walls near current price
            const currentPrice = (orderBook.bids[0][0] + orderBook.asks[0][0]) / 2;
            const nearbyAskWall = askWalls.find(([price]) => price < currentPrice * 1.02);
            const nearbyBidWall = bidWalls.find(([price]) => price > currentPrice * 0.98);
            
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
     * COMBINED ADVANCED SIGNAL
     * Merges all 3 analyses into one recommendation
     */
    async getAdvancedSignal(symbol, strategy) {
        const [orderBook, multiTF, supportResistance] = await Promise.all([
            this.analyzeOrderBook(symbol),
            this.multiTimeframeAnalysis(symbol, strategy),
            this.detectSupportResistance(symbol)
        ]);

        // Score calculation
        let buyScore = 0;
        let sellScore = 0;
        let reasons = [];

        // Order book contribution (weight: 30%)
        if (orderBook.signal === 'bullish') {
            buyScore += orderBook.strength * 0.3;
            reasons.push(`OrderBook: ${orderBook.reason}`);
        } else if (orderBook.signal === 'bearish') {
            sellScore += orderBook.strength * 0.3;
            reasons.push(`OrderBook: ${orderBook.reason}`);
        }

        // Block buy if there's a nearby sell wall
        if (orderBook.hasNearbyResistance) {
            buyScore -= 0.2;
            reasons.push('⚠️ Sell wall detected nearby');
        }

        // Multi-timeframe contribution (weight: 40%)
        if (multiTF.signal === 'bullish') {
            buyScore += multiTF.confidence * 0.4;
            reasons.push(`MTF: ${multiTF.reason}`);
        } else if (multiTF.signal === 'bearish') {
            sellScore += multiTF.confidence * 0.4;
            reasons.push(`MTF: ${multiTF.reason}`);
        }

        // Bonus for aligned timeframes
        if (multiTF.aligned) {
            if (multiTF.signal === 'bullish') buyScore += 0.15;
            if (multiTF.signal === 'bearish') sellScore += 0.15;
            reasons.push('✓ All timeframes aligned');
        }

        // Support/Resistance contribution (weight: 30%)
        if (supportResistance.nearSupport) {
            buyScore += 0.2;
            reasons.push('Near support level (good entry)');
        }
        if (supportResistance.nearResistance) {
            sellScore += 0.2;
            reasons.push('Near resistance level (take profit zone)');
        }

        // Determine final signal
        let signal = 'HOLD';
        let strength = 0;
        
        if (buyScore > sellScore && buyScore > 0.4) {
            signal = 'BUY';
            strength = Math.min(buyScore, 1);
        } else if (sellScore > buyScore && sellScore > 0.4) {
            signal = 'SELL';
            strength = Math.min(sellScore, 1);
        }

        return {
            symbol,
            signal,
            strength,
            buyScore,
            sellScore,
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
