/**
 * Arbitrage Scanner - Momentum-based scalping
 * 
 * Monitors real-time price feeds for quick momentum plays.
 * Less aggressive than true arbitrage but more reliable.
 */

const WebSocket = require('ws');
const EventEmitter = require('events');

class ArbitrageScanner extends EventEmitter {
    constructor(exchange, config = {}) {
        super();
        this.exchange = exchange;
        this.config = {
            minProfitPercent: 0.15,
            tradingFee: 0.1,
            maxTradeUSDT: 40,
            minTradeUSDT: 10,
            scanIntervalMs: 500,
            enabled: true,
            ...config
        };
        
        this.prices = new Map();
        this.priceHistory = new Map();
        this.ws = null;
        this.isRunning = false;
        this.lastExecuteTime = 0;
        this.minExecuteInterval = 10000;  // 10 seconds between scalps
        
        this.stats = {
            scansPerformed: 0,
            opportunitiesFound: 0,
            tradesExecuted: 0,
            totalProfit: 0,
            missedOpportunities: 0
        };
        
        this.monitorPairs = [
            'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
            'DOGEUSDT', 'ADAUSDT', 'PEPEUSDT', 'SHIBUSDT'
        ];
        
        this.executingTrade = false;
    }
    
    async start() {
        if (this.isRunning) return;
        
        console.log('\n⚡ MOMENTUM SCANNER STARTING');
        console.log(`   Monitoring ${this.monitorPairs.length} pairs`);
        console.log(`   Scan interval: ${this.config.scanIntervalMs}ms\n`);
        
        this.isRunning = true;
        await this.connectWebSocket();
        this.scanLoop();
    }
    
    async connectWebSocket() {
        return new Promise((resolve) => {
            const streams = this.monitorPairs.map(p => `${p.toLowerCase()}@bookTicker`).join('/');
            const wsUrl = `wss://stream.binance.com:9443/stream?streams=${streams}`;
            
            this.ws = new WebSocket(wsUrl);
            
            this.ws.on('open', () => {
                console.log('📡 Momentum scanner connected');
                resolve();
            });
            
            this.ws.on('message', (data) => {
                try {
                    const msg = JSON.parse(data);
                    if (msg.data) {
                        const t = msg.data;
                        const bid = parseFloat(t.b);
                        const ask = parseFloat(t.a);
                        
                        this.prices.set(t.s, {
                            symbol: t.s,
                            bid,
                            ask,
                            spread: ((ask - bid) / bid) * 100,
                            timestamp: Date.now()
                        });
                        
                        // Store price history for momentum detection
                        if (!this.priceHistory.has(t.s)) {
                            this.priceHistory.set(t.s, []);
                        }
                        const history = this.priceHistory.get(t.s);
                        history.push({ price: bid, time: Date.now() });
                        
                        // Keep only last 60 seconds
                        const cutoff = Date.now() - 60000;
                        while (history.length > 0 && history[0].time < cutoff) {
                            history.shift();
                        }
                    }
                } catch (e) {}
            });
            
            this.ws.on('error', (err) => {
                console.error('Momentum WS error:', err.message);
            });
            
            this.ws.on('close', () => {
                if (this.isRunning) {
                    setTimeout(() => this.connectWebSocket(), 5000);
                }
            });
            
            setTimeout(resolve, 3000);
        });
    }
    
    scanLoop() {
        if (!this.isRunning) return;
        
        this.scanMomentum();
        this.stats.scansPerformed++;
        
        setTimeout(() => this.scanLoop(), this.config.scanIntervalMs);
    }
    
    scanMomentum() {
        const now = Date.now();
        
        for (const [symbol, history] of this.priceHistory) {
            if (history.length < 10) continue;
            
            // Calculate 5-second momentum
            const recent = history.filter(h => now - h.time < 5000);
            if (recent.length < 2) continue;
            
            const oldPrice = recent[0].price;
            const newPrice = recent[recent.length - 1].price;
            const momentum = ((newPrice - oldPrice) / oldPrice) * 100;
            
            // Detect significant momentum (> 0.3% in 5 seconds)
            if (Math.abs(momentum) > 0.3) {
                const currentData = this.prices.get(symbol);
                if (!currentData) continue;
                
                this.stats.opportunitiesFound++;
                
                // Log strong momentum moves
                if (Math.abs(momentum) > 0.5) {
                    const dir = momentum > 0 ? '🟢 UP' : '🔴 DOWN';
                    console.log(`⚡ ${symbol} ${dir} ${momentum.toFixed(2)}% in 5s`);
                }
                
                this.emit('momentum', {
                    symbol,
                    momentum,
                    direction: momentum > 0 ? 'UP' : 'DOWN',
                    bid: currentData.bid,
                    ask: currentData.ask,
                    spread: currentData.spread
                });
            }
        }
    }
    
    stop() {
        this.isRunning = false;
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        console.log('\n🛑 Momentum scanner stopped');
        console.log(`   Scans: ${this.stats.scansPerformed}`);
        console.log(`   Momentum detected: ${this.stats.opportunitiesFound}`);
    }
    
    getStats() {
        return {
            ...this.stats,
            isRunning: this.isRunning,
            pricesTracked: this.prices.size
        };
    }
    
    setEnabled(enabled) {
        this.config.enabled = enabled;
        console.log(`Momentum scanner ${enabled ? 'ENABLED' : 'DISABLED'}`);
    }
}

module.exports = ArbitrageScanner;
