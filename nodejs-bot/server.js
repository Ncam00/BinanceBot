#!/usr/bin/env node
/**
 * Binance Trading Bot - Server Entry Point
 * =========================================
 * Automated cryptocurrency trading with RSI + MACD + EMA strategy
 * 
 * Usage:
 *   npm start        - Start in paper trading mode
 *   npm run start:live - Start in LIVE mode
 */

require('dotenv').config();
const express = require('express');
const cors = require('cors');
const chalk = require('chalk');

const Exchange = require('./src/exchange');
const Strategy = require('./src/strategy');
const RiskManager = require('./src/riskManager');
const Trader = require('./src/trader');
const Database = require('./src/database');
const ArbitrageScanner = require('./src/arbitrage');

// Configuration
const CONFIG = {
    port: parseInt(process.env.PORT) || 3000,
    tradingMode: process.argv.includes('--live') ? 'live' : (process.env.TRADING_MODE || 'paper'),
    tradingPairs: (process.env.TRADING_PAIRS || 'BTCUSDT,ETHUSDT,SOLUSDT').split(','),
    candleInterval: process.env.CANDLE_INTERVAL || '3m',
    
    // Risk settings
    maxPositionSizePercent: parseFloat(process.env.MAX_POSITION_SIZE_PERCENT) || 15,
    maxConcurrentPositions: parseInt(process.env.MAX_CONCURRENT_POSITIONS) || 5,
    stopLossPercent: parseFloat(process.env.STOP_LOSS_PERCENT) || 3,
    takeProfitPercent: parseFloat(process.env.TAKE_PROFIT_PERCENT) || 5,
    dailyLossLimitPercent: parseFloat(process.env.DAILY_LOSS_LIMIT_PERCENT) || 8,
    
    // Daily Profit Goal Settings (NZD)
    dailyProfitGoalMin: parseFloat(process.env.DAILY_PROFIT_GOAL_MIN) || 2,
    dailyProfitGoalMax: parseFloat(process.env.DAILY_PROFIT_GOAL_MAX) || 10,
    
    // Trailing Stop settings (LOCK IN PROFITS!)
    trailingStopEnabled: process.env.TRAILING_STOP_ENABLED === 'true',
    trailingStopActivation: parseFloat(process.env.TRAILING_STOP_ACTIVATION) || 2,  // Activate after 2% profit
    trailingStopCallback: parseFloat(process.env.TRAILING_STOP_CALLBACK) || 1.5,    // 1.5% trailing distance
    
    // Strategy settings (AGGRESSIVE)
    rsiPeriod: parseInt(process.env.RSI_PERIOD) || 14,
    rsiOversold: parseInt(process.env.RSI_OVERSOLD) || 40,
    rsiOverbought: parseInt(process.env.RSI_OVERBOUGHT) || 60,
    emaFast: parseInt(process.env.EMA_FAST) || 7,
    emaSlow: parseInt(process.env.EMA_SLOW) || 18,
    
    // Signal thresholds (LOWERED for more trades)
    buySignalThreshold: parseFloat(process.env.BUY_SIGNAL_THRESHOLD) || 0.4,
    sellSignalThreshold: parseFloat(process.env.SELL_SIGNAL_THRESHOLD) || 0.45,
};

// Global state
let bot = null;
let arbitrageScanner = null;
let isRunning = false;
let isPaused = false;

// Express app setup
const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static('public'));

// Print banner
function printBanner() {
    console.log(chalk.cyan(`
╔════════════════════════════════════════════════════════════════════╗
║          ${chalk.bold.white('BINANCE CROSSOVER BOT')} - IMPROVED NODE.JS              ║
╠════════════════════════════════════════════════════════════════════╣
║  Mode: ${CONFIG.tradingMode === 'live' ? chalk.red('★ LIVE (REAL MONEY) ★') : chalk.green('PAPER (SIMULATION)')}                          ║
║  Strategy: RSI + MACD + EMA Crossover + Volume                     ║
║  Pairs: ${chalk.yellow(CONFIG.tradingPairs.join(', '))}                                      
║  Risk: Stop-Loss ${CONFIG.stopLossPercent}% | Take-Profit ${CONFIG.takeProfitPercent}%                         ║
╠════════════════════════════════════════════════════════════════════╣
║  Server: ${chalk.green(`http://localhost:${CONFIG.port}`)}                                ║
╚════════════════════════════════════════════════════════════════════╝
`));

    console.log(chalk.cyan(`
┌────────────────────────────────────────────────────────────────────┐
│  RSI Filter: BUY when RSI < ${CONFIG.rsiOversold}, SELL when RSI > ${CONFIG.rsiOverbought}               │
│  EMA Crossover: Fast(${CONFIG.emaFast}) / Slow(${CONFIG.emaSlow})                               │
│  Max Position: ${CONFIG.maxPositionSizePercent}% of portfolio per trade                          │
│  Max Concurrent: ${CONFIG.maxConcurrentPositions} positions                                       │
└────────────────────────────────────────────────────────────────────┘
`));
}

// Initialize bot
async function initializeBot() {
    console.log(chalk.yellow('\n🔧 Initializing bot components...'));
    
    // Initialize database
    const db = new Database();
    console.log(chalk.green('  ✓ Database initialized'));
    
    // Initialize exchange
    const exchange = new Exchange(
        process.env.BINANCE_API_KEY,
        process.env.BINANCE_SECRET_KEY,
        CONFIG.tradingMode === 'paper'
    );
    
    // Test connection
    const connected = await exchange.testConnection();
    if (!connected && CONFIG.tradingMode === 'live') {
        console.log(chalk.red('\n❌ Failed to connect to Binance API!'));
        console.log(chalk.yellow('   Check your API keys in .env file'));
        process.exit(1);
    }
    console.log(chalk.green('  ✓ Exchange connection established'));
    
    // Get FULL account balance (all assets)
    let balance = 0;
    let portfolioInfo = null;
    
    if (CONFIG.tradingMode === 'live') {
        portfolioInfo = await exchange.getTotalBalanceUSDT();
        balance = portfolioInfo.freeUSDT;
        console.log(chalk.green(`  ✓ Total Portfolio Value: $${portfolioInfo.totalUSDT.toFixed(2)} USDT`));
        console.log(chalk.green(`  ✓ Available for Trading: $${balance.toFixed(2)} USDT`));
    } else {
        balance = await exchange.getBalance();
        console.log(chalk.green(`  ✓ Paper Balance: $${balance.toFixed(2)} USDT`));
    }
    
    // Initialize strategy
    const strategy = new Strategy(CONFIG);
    console.log(chalk.green('  ✓ Trading strategy loaded'));
    
    // Initialize risk manager with actual balance
    const initialCapital = parseFloat(process.env.INITIAL_CAPITAL) || balance;
    const riskManager = new RiskManager(CONFIG, initialCapital);
    console.log(chalk.green(`  ✓ Risk manager configured (Locked capital: $${initialCapital.toFixed(2)})`));
    
    // Initialize trader
    const trader = new Trader(exchange, strategy, riskManager, db, CONFIG);
    console.log(chalk.green('  ✓ Trader module ready'));
    
    return trader;
}

// Main trading loop
async function runTradingLoop() {
    if (!isRunning || isPaused) return;
    
    try {
        const results = await bot.executeTradingCycle();
        
        // Log results
        if (results.signals.length > 0) {
            results.signals.forEach(signal => {
                const emoji = signal.action === 'BUY' ? '🟢' : signal.action === 'SELL' ? '🔴' : '⚪';
                console.log(chalk.white(`${emoji} ${signal.symbol}: ${signal.action} signal (strength: ${signal.strength.toFixed(2)})`));
            });
        }
        
        if (results.trades.length > 0) {
            results.trades.forEach(trade => {
                const color = trade.side === 'BUY' ? chalk.green : chalk.red;
                console.log(color(`  ★ TRADE EXECUTED: ${trade.side} ${trade.amount} ${trade.symbol} @ $${trade.price.toFixed(2)}`));
            });
        }
        
        // Check positions for stop-loss/take-profit
        await bot.checkPositions();
        
    } catch (error) {
        console.error(chalk.red(`❌ Error in trading loop: ${error.message}`));
    }
    
    // Schedule next iteration (15 seconds for SCALPING MODE!)
    if (isRunning && !isPaused) {
        const interval = parseInt(process.env.ANALYSIS_INTERVAL) || 15000;
        setTimeout(runTradingLoop, interval); // Fast trading!
    }
}

// API Routes
app.get('/api/status', (req, res) => {
    res.json({
        running: isRunning,
        paused: isPaused,
        mode: CONFIG.tradingMode,
        pairs: CONFIG.tradingPairs,
        uptime: process.uptime(),
        settings: {
            buyThreshold: CONFIG.buySignalThreshold,
            sellThreshold: CONFIG.sellSignalThreshold,
            trailingStopEnabled: CONFIG.trailingStopEnabled,
            trailingStopActivation: CONFIG.trailingStopActivation,
            stopLoss: CONFIG.stopLossPercent,
            takeProfit: CONFIG.takeProfitPercent
        }
    });
});

// Mobile-friendly comprehensive status endpoint
app.get('/api/mobile', async (req, res) => {
    try {
        const balance = bot ? await bot.exchange.getBalance() : 0;
        const positions = bot ? bot.getOpenPositions() : [];
        const stats = bot ? bot.db.getStats() : {};
        const trades = bot ? bot.db.getRecentTrades(5) : [];
        let portfolio = null;
        
        if (bot && CONFIG.tradingMode === 'live') {
            try {
                portfolio = await bot.exchange.getTotalBalanceUSDT();
            } catch (e) {}
        }
        
        const profitInfo = bot?.riskManager ? {
            lockedCapital: bot.riskManager.lockedTradingCapital,
            withdrawable: bot.riskManager.withdrawableProfit,
            totalRealized: bot.riskManager.totalRealizedProfit
        } : null;
        
        res.json({
            status: {
                running: isRunning,
                paused: isPaused,
                mode: CONFIG.tradingMode,
                uptime: Math.floor(process.uptime())
            },
            balance: {
                available: balance,
                total: portfolio?.totalUSDT || balance,
                currency: 'USDT'
            },
            positions: positions.map(p => ({
                symbol: p.symbol,
                amount: p.amount,
                entry: p.entryPrice,
                current: p.currentPrice,
                pnl: p.pnl,
                pnlPercent: p.pnlPercent,
                trailingStop: p.trailingStopActive ? p.trailingStopPrice : null
            })),
            stats: {
                trades: stats.totalTrades || 0,
                winRate: stats.winRate || 0,
                todayPnL: stats.todayPnL || 0
            },
            recentTrades: trades,
            profit: profitInfo,
            timestamp: new Date().toISOString()
        });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/api/balance', async (req, res) => {
    try {
        const balance = bot ? await bot.exchange.getBalance() : 0;
        res.json({ balance, currency: 'USDT' });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/api/portfolio', async (req, res) => {
    try {
        if (!bot) {
            return res.json({ totalUSDT: 0, holdings: [], freeUSDT: 0 });
        }
        const portfolio = await bot.exchange.getTotalBalanceUSDT();
        res.json(portfolio);
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/api/positions', async (req, res) => {
    try {
        const positions = bot ? bot.getOpenPositions() : [];
        res.json({ positions });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/api/trades', (req, res) => {
    try {
        const limit = parseInt(req.query.limit) || 50;
        const trades = bot ? bot.db.getRecentTrades(limit) : [];
        res.json({ trades });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/api/stats', (req, res) => {
    try {
        const stats = bot ? bot.db.getStats() : {};
        res.json({ stats });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.post('/api/pause', (req, res) => {
    isPaused = true;
    console.log(chalk.yellow('\n⏸️  Trading PAUSED by user'));
    res.json({ success: true, paused: true });
});

app.post('/api/resume', (req, res) => {
    isPaused = false;
    console.log(chalk.green('\n▶️  Trading RESUMED'));
    runTradingLoop();
    res.json({ success: true, paused: false });
});

app.post('/api/stop', (req, res) => {
    isRunning = false;
    if (arbitrageScanner) arbitrageScanner.stop();
    console.log(chalk.red('\n🛑 Bot STOPPED by user'));
    res.json({ success: true, running: false });
});

// Arbitrage API Routes
app.get('/api/arbitrage/status', (req, res) => {
    if (!arbitrageScanner) {
        return res.json({ enabled: false, message: 'Arbitrage scanner not initialized' });
    }
    res.json(arbitrageScanner.getStats());
});

app.post('/api/arbitrage/enable', (req, res) => {
    if (!arbitrageScanner) {
        return res.status(400).json({ error: 'Arbitrage scanner not initialized' });
    }
    arbitrageScanner.setEnabled(true);
    res.json({ success: true, enabled: true });
});

app.post('/api/arbitrage/disable', (req, res) => {
    if (!arbitrageScanner) {
        return res.status(400).json({ error: 'Arbitrage scanner not initialized' });
    }
    arbitrageScanner.setEnabled(false);
    res.json({ success: true, enabled: false });
});

// Profit Protection API Routes
app.get('/api/profit', (req, res) => {
    try {
        if (!bot || !bot.riskManager) {
            return res.json({
                lockedCapital: 0,
                withdrawableProfit: 0,
                totalRealizedProfit: 0,
                profitWithdrawn: 0
            });
        }
        
        const status = bot.riskManager.getStatus();
        res.json({
            lockedCapital: status.lockedTradingCapital,
            withdrawableProfit: status.withdrawableProfit,
            totalRealizedProfit: status.totalRealizedProfit,
            profitWithdrawn: status.profitWithdrawn,
            currentBalance: status.currentBalance
        });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.post('/api/profit/withdraw', (req, res) => {
    try {
        const { amount } = req.body;
        
        if (!bot || !bot.riskManager) {
            return res.status(400).json({ error: 'Bot not initialized' });
        }
        
        const withdrawAmount = amount || bot.riskManager.withdrawableProfit;
        bot.riskManager.markProfitWithdrawn(withdrawAmount);
        
        res.json({
            success: true,
            withdrawn: withdrawAmount,
            remaining: bot.riskManager.withdrawableProfit
        });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

// Daily Goal Tracking API
app.get('/api/daily-goal', (req, res) => {
    try {
        if (!bot || !bot.riskManager) {
            return res.json({
                dailyPnLUSD: 0,
                dailyPnLNZD: 0,
                goalMinNZD: 2,
                goalMaxNZD: 10,
                goalProgress: 0,
                goalHit: false,
                conservativeMode: false,
                todaysTrades: 0,
                todaysProfits: []
            });
        }
        
        const status = bot.riskManager.getStatus();
        res.json({
            dailyPnLUSD: status.dailyPnL,
            dailyPnLNZD: status.dailyProfitNZD,
            goalMinNZD: status.dailyProfitGoalMinNZD,
            goalMaxNZD: status.dailyProfitGoalMaxNZD,
            goalProgress: status.goalProgress,
            goalHit: status.goalHitToday,
            conservativeMode: status.conservativeMode,
            todaysTrades: status.dailyTrades,
            todaysProfits: status.todaysProfits || [],
            message: status.goalHitToday 
                ? `🎉 Daily goal met! ($${status.dailyProfitNZD.toFixed(2)} NZD)` 
                : `Progress: ${status.goalProgress.toFixed(0)}% toward $${status.dailyProfitGoalMinNZD} NZD`
        });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

// Dashboard HTML
app.get('/', (req, res) => {
    res.send(`
<!DOCTYPE html>
<html>
<head>
    <title>Binance Trading Bot</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Arial, sans-serif; 
            background: #0d1117; 
            color: #c9d1d9; 
            margin: 0;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #58a6ff; text-align: center; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
        .card { 
            background: #161b22; 
            border: 1px solid #30363d; 
            border-radius: 8px; 
            padding: 20px; 
        }
        .card h2 { color: #58a6ff; margin-top: 0; font-size: 1.2em; }
        .stat { font-size: 2em; font-weight: bold; color: #7ee787; }
        .stat.negative { color: #f85149; }
        .btn { 
            padding: 10px 20px; 
            border: none; 
            border-radius: 6px; 
            cursor: pointer; 
            font-size: 14px;
            margin: 5px;
        }
        .btn-green { background: #238636; color: white; }
        .btn-yellow { background: #9e6a03; color: white; }
        .btn-red { background: #da3633; color: white; }
        .btn:hover { opacity: 0.8; }
        .status { display: inline-block; padding: 5px 10px; border-radius: 4px; }
        .status.running { background: #238636; }
        .status.paused { background: #9e6a03; }
        .status.stopped { background: #da3633; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #30363d; }
        th { color: #8b949e; }
        .trade-buy { color: #7ee787; }
        .trade-sell { color: #f85149; }
        #log { 
            background: #0d1117; 
            border: 1px solid #30363d; 
            padding: 10px; 
            height: 200px; 
            overflow-y: auto; 
            font-family: monospace;
            font-size: 12px;
        }
        .mode-live { color: #f85149; font-weight: bold; }
        .mode-paper { color: #7ee787; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Binance Trading Bot</h1>
        
        <div class="grid">
            <div class="card">
                <h2>Status</h2>
                <p>Mode: <span id="mode" class="mode-paper">Loading...</span></p>
                <p>Status: <span id="status" class="status">Loading...</span></p>
                <p>Uptime: <span id="uptime">0s</span></p>
                <div>
                    <button class="btn btn-green" onclick="resume()">▶ Resume</button>
                    <button class="btn btn-yellow" onclick="pause()">⏸ Pause</button>
                    <button class="btn btn-red" onclick="stop()">⏹ Stop</button>
                </div>
            </div>
            
            <div class="card">
                <h2>Balance</h2>
                <p class="stat">$<span id="balance">0.00</span></p>
                <p>USDT</p>
            </div>
            
            <div class="card">
                <h2>Today's P/L</h2>
                <p class="stat" id="pnl">$0.00</p>
                <p id="pnl-percent">0.00%</p>
            </div>
            
            <div class="card">
                <h2>Statistics</h2>
                <p>Total Trades: <span id="total-trades">0</span></p>
                <p>Win Rate: <span id="win-rate">0%</span></p>
                <p>Avg Profit: <span id="avg-profit">$0.00</span></p>
            </div>
        </div>
        
        <div class="card" style="margin-top: 20px;">
            <h2>Open Positions</h2>
            <table>
                <thead>
                    <tr><th>Symbol</th><th>Side</th><th>Amount</th><th>Entry</th><th>Current</th><th>P/L</th></tr>
                </thead>
                <tbody id="positions"></tbody>
            </table>
        </div>
        
        <div class="card" style="margin-top: 20px;">
            <h2>Recent Trades</h2>
            <table>
                <thead>
                    <tr><th>Time</th><th>Symbol</th><th>Side</th><th>Amount</th><th>Price</th><th>P/L</th></tr>
                </thead>
                <tbody id="trades"></tbody>
            </table>
        </div>
        
        <div class="card" style="margin-top: 20px;">
            <h2>Activity Log</h2>
            <div id="log"></div>
        </div>
    </div>
    
    <script>
        async function fetchData() {
            try {
                const [status, balance, positions, trades, stats] = await Promise.all([
                    fetch('/api/status').then(r => r.json()),
                    fetch('/api/balance').then(r => r.json()),
                    fetch('/api/positions').then(r => r.json()),
                    fetch('/api/trades?limit=10').then(r => r.json()),
                    fetch('/api/stats').then(r => r.json())
                ]);
                
                // Update status
                document.getElementById('mode').textContent = status.mode.toUpperCase();
                document.getElementById('mode').className = status.mode === 'live' ? 'mode-live' : 'mode-paper';
                
                const statusText = status.paused ? 'PAUSED' : (status.running ? 'RUNNING' : 'STOPPED');
                const statusEl = document.getElementById('status');
                statusEl.textContent = statusText;
                statusEl.className = 'status ' + statusText.toLowerCase();
                
                document.getElementById('uptime').textContent = Math.floor(status.uptime) + 's';
                
                // Update balance
                document.getElementById('balance').textContent = balance.balance.toFixed(2);
                
                // Update stats
                if (stats.stats) {
                    document.getElementById('total-trades').textContent = stats.stats.totalTrades || 0;
                    document.getElementById('win-rate').textContent = (stats.stats.winRate || 0).toFixed(1) + '%';
                    document.getElementById('avg-profit').textContent = '$' + (stats.stats.avgProfit || 0).toFixed(2);
                    
                    const pnl = stats.stats.todayPnL || 0;
                    const pnlEl = document.getElementById('pnl');
                    pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
                    pnlEl.className = 'stat' + (pnl < 0 ? ' negative' : '');
                }
                
                // Update positions
                const positionsEl = document.getElementById('positions');
                positionsEl.innerHTML = positions.positions.length === 0 
                    ? '<tr><td colspan="6" style="text-align:center;color:#8b949e;">No open positions</td></tr>'
                    : positions.positions.map(p => \`
                        <tr>
                            <td>\${p.symbol}</td>
                            <td class="trade-\${p.side.toLowerCase()}">\${p.side}</td>
                            <td>\${p.amount}</td>
                            <td>$\${p.entryPrice.toFixed(2)}</td>
                            <td>$\${p.currentPrice?.toFixed(2) || '-'}</td>
                            <td class="\${p.pnl >= 0 ? 'trade-buy' : 'trade-sell'}">\${p.pnl >= 0 ? '+' : ''}$\${p.pnl?.toFixed(2) || '0.00'}</td>
                        </tr>
                    \`).join('');
                
                // Update trades
                const tradesEl = document.getElementById('trades');
                tradesEl.innerHTML = trades.trades.length === 0
                    ? '<tr><td colspan="6" style="text-align:center;color:#8b949e;">No trades yet</td></tr>'
                    : trades.trades.map(t => \`
                        <tr>
                            <td>\${new Date(t.timestamp).toLocaleTimeString()}</td>
                            <td>\${t.symbol}</td>
                            <td class="trade-\${t.side.toLowerCase()}">\${t.side}</td>
                            <td>\${t.amount}</td>
                            <td>$\${t.price.toFixed(2)}</td>
                            <td class="\${(t.pnl || 0) >= 0 ? 'trade-buy' : 'trade-sell'}">\${t.pnl ? ((t.pnl >= 0 ? '+' : '') + '$' + t.pnl.toFixed(2)) : '-'}</td>
                        </tr>
                    \`).join('');
                    
            } catch (error) {
                console.error('Error fetching data:', error);
            }
        }
        
        async function pause() {
            await fetch('/api/pause', { method: 'POST' });
            addLog('⏸️ Paused trading');
            fetchData();
        }
        
        async function resume() {
            await fetch('/api/resume', { method: 'POST' });
            addLog('▶️ Resumed trading');
            fetchData();
        }
        
        async function stop() {
            if (confirm('Are you sure you want to stop the bot?')) {
                await fetch('/api/stop', { method: 'POST' });
                addLog('🛑 Bot stopped');
                fetchData();
            }
        }
        
        function addLog(msg) {
            const log = document.getElementById('log');
            const time = new Date().toLocaleTimeString();
            log.innerHTML = \`[\${time}] \${msg}\\n\` + log.innerHTML;
        }
        
        // Initial load and refresh every 5 seconds
        fetchData();
        setInterval(fetchData, 5000);
        addLog('Dashboard connected');
    </script>
</body>
</html>
    `);
});

// Start server
async function main() {
    printBanner();
    
    // Check for live mode confirmation
    if (CONFIG.tradingMode === 'live') {
        // Auto-confirm if CONFIRM_LIVE=true in environment or --confirm-live flag
        const autoConfirm = process.env.CONFIRM_LIVE === 'true' || process.argv.includes('--confirm-live');
        
        if (!autoConfirm) {
            const readline = require('readline');
            const rl = readline.createInterface({
                input: process.stdin,
                output: process.stdout
            });
            
            console.log(chalk.red.bold('\n⚠️  WARNING: You are about to start LIVE TRADING with REAL MONEY!'));
            console.log(chalk.red('    You could lose some or ALL of your funds.'));
            console.log(chalk.yellow('\n    Type "YES I UNDERSTAND" to continue: '));
            
            const answer = await new Promise(resolve => rl.question('', resolve));
            rl.close();
            
            if (answer !== 'YES I UNDERSTAND') {
                console.log(chalk.yellow('\n✋ Live trading cancelled. Starting in PAPER mode instead.\n'));
                CONFIG.tradingMode = 'paper';
            }
        } else {
            console.log(chalk.yellow('\n⚡ Auto-confirmed LIVE trading mode via environment/flag'));
            console.log(chalk.red.bold('   ⚠️ TRADING WITH REAL MONEY!\n'));
        }
    }
    
    try {
        bot = await initializeBot();
        isRunning = true;
        
        app.listen(CONFIG.port, () => {
            console.log(chalk.green(`\n✅ Server running at http://localhost:${CONFIG.port}`));
            console.log(chalk.cyan('   Dashboard available in your browser\n'));
            console.log(chalk.white('─'.repeat(70)));
            console.log(chalk.cyan('\n🚀 Starting trading loop...\n'));
            
            runTradingLoop();
            
            // Start Arbitrage Scanner (millisecond trading)
            if (CONFIG.tradingMode === 'live') {
                console.log(chalk.magenta('\n⚡ Starting Arbitrage Scanner...'));
                arbitrageScanner = new ArbitrageScanner(bot.exchange, {
                    minProfitPercent: 0.12,  // 0.12% min profit after fees
                    tradingFee: 0.1,         // Binance 0.1% fee
                    maxTradeUSDT: 40,        // Max per arbitrage trade
                    minTradeUSDT: 10,        // Min per trade
                    scanIntervalMs: 50,      // Scan every 50ms!
                    enabled: true            // Start enabled
                });
                
                arbitrageScanner.on('opportunity', (opp) => {
                    console.log(chalk.magenta(`⚡ ARB: ${opp.type} ${opp.profitPercent.toFixed(3)}% potential`));
                });
                
                arbitrageScanner.on('trade', (trade) => {
                    console.log(chalk.magenta.bold(`💰 ARB PROFIT: $${trade.profit.toFixed(4)} in ${trade.executionTime}ms`));
                    bot.riskManager.addProfit(trade.profit);
                });
                
                arbitrageScanner.start().catch(err => {
                    console.log(chalk.yellow(`⚠️ Arbitrage scanner error: ${err.message}`));
                });
            }
        });
        
    } catch (error) {
        console.error(chalk.red(`\n❌ Failed to start bot: ${error.message}`));
        process.exit(1);
    }
}

// Handle graceful shutdown
process.on('SIGINT', () => {
    console.log(chalk.yellow('\n\n🛑 Shutting down gracefully...'));
    isRunning = false;
    setTimeout(() => process.exit(0), 1000);
});

main();
