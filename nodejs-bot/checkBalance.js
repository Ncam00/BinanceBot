const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '.env') });
const ccxt = require(path.join(__dirname, 'node_modules', 'ccxt'));

(async () => {
    const exchange = new ccxt.binance({
        apiKey: process.env.BINANCE_API_KEY,
        secret: process.env.BINANCE_SECRET_KEY
    });

    const balance = await exchange.fetchBalance();
    const usdt = balance.free['USDT'] || 0;
    const total = balance.total;

    let holdings = [];
    for (let [k, v] of Object.entries(total)) {
        if (v > 0 && k !== 'USDT') {
            holdings.push(k + ': ' + v);
        }
    }

    console.log('USDT Available: $' + Number(usdt).toFixed(2));
    console.log('Holdings:', holdings.join(', '));

    const tickers = await exchange.fetchTickers();
    let portfolioValue = usdt;
    for (let [k, v] of Object.entries(total)) {
        if (v > 0 && k !== 'USDT') {
            const sym = k + '/USDT';
            if (tickers[sym]) {
                portfolioValue += v * tickers[sym].last;
            }
        }
    }
    console.log('Total Portfolio: $' + Number(portfolioValue).toFixed(2));
})();
