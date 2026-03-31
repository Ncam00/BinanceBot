/**
 * Binance Exchange API Wrapper
 * ============================
 * Handles all communication with Binance API
 */

const Binance = require('binance-api-node').default;

class Exchange {
    constructor(apiKey, secretKey, paperMode = true) {
        this.paperMode = paperMode;
        this.paperBalance = 300; // Starting paper balance in USDT
        this.paperPositions = [];
        
        // Initialize Binance client
        if (!paperMode && apiKey && secretKey) {
            this.client = Binance({
                apiKey: apiKey,
                apiSecret: secretKey,
            });
        } else {
            // Public client for market data
            this.client = Binance();
        }
        
        this.priceCache = {};
        this.lastPriceFetch = 0;
    }
    
    /**
     * Test API connection
     */
    async testConnection() {
        try {
            await this.client.time();
            return true;
        } catch (error) {
            console.error('Connection test failed:', error.message);
            return false;
        }
    }
    
    /**
     * Get account balance in USDT (including all assets converted to USDT value)
     */
    async getBalance() {
        if (this.paperMode) {
            return this.paperBalance;
        }
        
        try {
            const account = await this.client.accountInfo();
            const usdtBalance = account.balances.find(b => b.asset === 'USDT');
            return parseFloat(usdtBalance?.free || 0);
        } catch (error) {
            console.error('Failed to get balance:', error.message);
            return 0;
        }
    }
    
    /**
     * Get TOTAL portfolio value (all assets converted to USDT)
     */
    async getTotalBalanceUSDT() {
        if (this.paperMode) {
            return this.paperBalance;
        }
        
        try {
            const account = await this.client.accountInfo();
            const allPrices = await this.client.prices();
            
            let totalUSDT = 0;
            const holdings = [];
            
            for (const balance of account.balances) {
                const free = parseFloat(balance.free);
                const locked = parseFloat(balance.locked);
                const total = free + locked;
                
                if (total > 0) {
                    let valueUSDT = 0;
                    
                    if (balance.asset === 'USDT') {
                        valueUSDT = total;
                    } else if (balance.asset === 'BUSD' || balance.asset === 'USDC') {
                        valueUSDT = total; // Stablecoins ~1:1
                    } else {
                        // Try to get USDT price
                        const usdtPair = `${balance.asset}USDT`;
                        if (allPrices[usdtPair]) {
                            valueUSDT = total * parseFloat(allPrices[usdtPair]);
                        }
                    }
                    
                    if (valueUSDT > 0.01) {
                        totalUSDT += valueUSDT;
                        holdings.push({
                            asset: balance.asset,
                            amount: total,
                            free: free,
                            locked: locked,
                            valueUSDT: valueUSDT
                        });
                    }
                }
            }
            
            console.log(`\n📊 Portfolio Holdings:`);
            holdings.forEach(h => {
                console.log(`   ${h.asset}: ${h.amount.toFixed(6)} (~$${h.valueUSDT.toFixed(2)} USDT)`);
            });
            console.log(`   💰 TOTAL: $${totalUSDT.toFixed(2)} USDT\n`);
            
            return { totalUSDT, holdings, freeUSDT: parseFloat(account.balances.find(b => b.asset === 'USDT')?.free || 0) };
        } catch (error) {
            console.error('Failed to get total balance:', error.message);
            return { totalUSDT: 0, holdings: [], freeUSDT: 0 };
        }
    }
    
    /**
     * Get available USDT for trading (free balance only)
     */
    async getAvailableUSDT() {
        if (this.paperMode) {
            return this.paperBalance;
        }
        
        try {
            const account = await this.client.accountInfo();
            const usdtBalance = account.balances.find(b => b.asset === 'USDT');
            return parseFloat(usdtBalance?.free || 0);
        } catch (error) {
            console.error('Failed to get available USDT:', error.message);
            return 0;
        }
    }
    
    /**
     * Get current price for a symbol
     */
    async getPrice(symbol) {
        try {
            const ticker = await this.client.prices({ symbol });
            const price = parseFloat(ticker[symbol]);
            this.priceCache[symbol] = price;
            return price;
        } catch (error) {
            console.error(`Failed to get price for ${symbol}:`, error.message);
            return this.priceCache[symbol] || 0;
        }
    }
    
    /**
     * Get prices for multiple symbols
     */
    async getPrices(symbols) {
        try {
            const allPrices = await this.client.prices();
            const result = {};
            symbols.forEach(symbol => {
                result[symbol] = parseFloat(allPrices[symbol] || 0);
                this.priceCache[symbol] = result[symbol];
            });
            return result;
        } catch (error) {
            console.error('Failed to get prices:', error.message);
            return symbols.reduce((acc, s) => ({ ...acc, [s]: this.priceCache[s] || 0 }), {});
        }
    }
    
    /**
     * Get historical candles (OHLCV data)
     */
    async getCandles(symbol, interval = '15m', limit = 100) {
        try {
            const candles = await this.client.candles({
                symbol,
                interval,
                limit
            });
            
            return candles.map(c => ({
                timestamp: c.openTime,
                open: parseFloat(c.open),
                high: parseFloat(c.high),
                low: parseFloat(c.low),
                close: parseFloat(c.close),
                volume: parseFloat(c.volume),
            }));
        } catch (error) {
            console.error(`Failed to get candles for ${symbol}:`, error.message);
            return [];
        }
    }
    
    /**
     * Get symbol info (minimum quantities, price precision, etc.)
     */
    async getSymbolInfo(symbol) {
        try {
            const info = await this.client.exchangeInfo();
            const symbolInfo = info.symbols.find(s => s.symbol === symbol);
            
            if (!symbolInfo) return null;
            
            const lotSizeFilter = symbolInfo.filters.find(f => f.filterType === 'LOT_SIZE');
            const priceFilter = symbolInfo.filters.find(f => f.filterType === 'PRICE_FILTER');
            const minNotionalFilter = symbolInfo.filters.find(f => f.filterType === 'NOTIONAL' || f.filterType === 'MIN_NOTIONAL');
            
            return {
                symbol,
                baseAsset: symbolInfo.baseAsset,
                quoteAsset: symbolInfo.quoteAsset,
                minQty: parseFloat(lotSizeFilter?.minQty || 0),
                maxQty: parseFloat(lotSizeFilter?.maxQty || 0),
                stepSize: parseFloat(lotSizeFilter?.stepSize || 0),
                tickSize: parseFloat(priceFilter?.tickSize || 0),
                minNotional: parseFloat(minNotionalFilter?.minNotional || minNotionalFilter?.notional || 10),
            };
        } catch (error) {
            console.error(`Failed to get symbol info for ${symbol}:`, error.message);
            return null;
        }
    }
    
    /**
     * Round quantity to valid step size
     */
    roundQuantity(quantity, stepSize) {
        const precision = Math.max(0, Math.round(-Math.log10(stepSize)));
        return Math.floor(quantity * Math.pow(10, precision)) / Math.pow(10, precision);
    }
    
    /**
     * Get valid quantity for a symbol (rounds to step size and checks min)
     */
    async getValidQuantity(symbol, quantity) {
        const info = await this.getSymbolInfo(symbol);
        if (!info) return quantity;
        
        // Round to step size
        let validQty = this.roundQuantity(quantity, info.stepSize);
        
        // Ensure minimum
        if (validQty < info.minQty) {
            return 0;
        }
        
        return validQty;
    }
    
    /**
     * Execute a market buy order
     */
    async marketBuy(symbol, quantity) {
        const price = await this.getPrice(symbol);
        
        // Get valid quantity for this symbol
        const validQty = await this.getValidQuantity(symbol, quantity);
        if (validQty <= 0) {
            throw new Error(`Quantity ${quantity} too small for ${symbol} (min lot size not met)`);
        }
        
        if (this.paperMode) {
            const cost = validQty * price;
            if (cost > this.paperBalance) {
                throw new Error('Insufficient paper balance');
            }
            
            this.paperBalance -= cost;
            this.paperPositions.push({
                symbol,
                side: 'BUY',
                amount: validQty,
                entryPrice: price,
                timestamp: Date.now()
            });
            
            return {
                symbol,
                side: 'BUY',
                amount: validQty,
                price,
                cost,
                orderId: `PAPER-${Date.now()}`,
                paper: true
            };
        }
        
        try {
            const order = await this.client.order({
                symbol,
                side: 'BUY',
                type: 'MARKET',
                quantity: validQty.toString()
            });
            
            const avgPrice = parseFloat(order.fills.reduce((sum, f) => 
                sum + parseFloat(f.price) * parseFloat(f.qty), 0) / 
                order.fills.reduce((sum, f) => sum + parseFloat(f.qty), 0));
            
            return {
                symbol,
                side: 'BUY',
                amount: parseFloat(order.executedQty),
                price: avgPrice,
                cost: avgPrice * parseFloat(order.executedQty),
                orderId: order.orderId,
                paper: false
            };
        } catch (error) {
            console.error(`Market buy failed for ${symbol}:`, error.message);
            throw error;
        }
    }
    
    /**
     * Execute a market sell order
     */
    async marketSell(symbol, quantity) {
        const price = await this.getPrice(symbol);
        
        // Get valid quantity for this symbol
        const validQty = await this.getValidQuantity(symbol, quantity);
        if (validQty <= 0) {
            throw new Error(`Quantity ${quantity} too small for ${symbol} (min lot size not met)`);
        }
        
        if (this.paperMode) {
            // Find and remove position
            const posIdx = this.paperPositions.findIndex(
                p => p.symbol === symbol && p.side === 'BUY'
            );
            
            if (posIdx === -1) {
                throw new Error('No position to sell');
            }
            
            const position = this.paperPositions[posIdx];
            const revenue = validQty * price;
            const pnl = revenue - (validQty * position.entryPrice);
            
            this.paperBalance += revenue;
            this.paperPositions.splice(posIdx, 1);
            
            return {
                symbol,
                side: 'SELL',
                amount: validQty,
                price,
                revenue,
                pnl,
                orderId: `PAPER-${Date.now()}`,
                paper: true
            };
        }
        
        try {
            const order = await this.client.order({
                symbol,
                side: 'SELL',
                type: 'MARKET',
                quantity: validQty.toString()
            });
            
            const avgPrice = parseFloat(order.fills.reduce((sum, f) => 
                sum + parseFloat(f.price) * parseFloat(f.qty), 0) / 
                order.fills.reduce((sum, f) => sum + parseFloat(f.qty), 0));
            
            return {
                symbol,
                side: 'SELL',
                amount: parseFloat(order.executedQty),
                price: avgPrice,
                revenue: avgPrice * parseFloat(order.executedQty),
                orderId: order.orderId,
                paper: false
            };
        } catch (error) {
            console.error(`Market sell failed for ${symbol}:`, error.message);
            throw error;
        }
    }
    
    /**
     * Get open orders
     */
    async getOpenOrders(symbol = null) {
        if (this.paperMode) {
            return this.paperPositions.filter(p => !symbol || p.symbol === symbol);
        }
        
        try {
            const params = symbol ? { symbol } : {};
            return await this.client.openOrders(params);
        } catch (error) {
            console.error('Failed to get open orders:', error.message);
            return [];
        }
    }
    
    /**
     * Get paper positions (for tracking)
     */
    getPaperPositions() {
        return this.paperPositions;
    }
    
    /**
     * Update paper position prices
     */
    async updatePaperPositionPrices() {
        if (!this.paperMode || this.paperPositions.length === 0) return;
        
        const symbols = [...new Set(this.paperPositions.map(p => p.symbol))];
        const prices = await this.getPrices(symbols);
        
        this.paperPositions.forEach(pos => {
            pos.currentPrice = prices[pos.symbol];
            pos.pnl = (pos.currentPrice - pos.entryPrice) * pos.amount;
            pos.pnlPercent = ((pos.currentPrice - pos.entryPrice) / pos.entryPrice) * 100;
        });
    }
    
    /**
     * Create a market order (used by arbitrage scanner)
     * @param {string} symbol - Trading pair (e.g., 'BTCUSDT')
     * @param {string} side - 'buy' or 'sell'
     * @param {number} quantity - Amount to trade
     */
    async createMarketOrder(symbol, side, quantity) {
        if (side.toLowerCase() === 'buy') {
            return await this.marketBuy(symbol, quantity);
        } else {
            return await this.marketSell(symbol, quantity);
        }
    }
    
    /**
     * Get real-time order book for a symbol
     */
    async getOrderBook(symbol, limit = 5) {
        try {
            const book = await this.client.book({ symbol, limit });
            return {
                bids: book.bids.map(b => ({ price: parseFloat(b.price), qty: parseFloat(b.quantity) })),
                asks: book.asks.map(a => ({ price: parseFloat(a.price), qty: parseFloat(a.quantity) }))
            };
        } catch (error) {
            console.error(`Failed to get order book for ${symbol}:`, error.message);
            return { bids: [], asks: [] };
        }
    }
}

module.exports = Exchange;
