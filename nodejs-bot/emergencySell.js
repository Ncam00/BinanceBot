// EMERGENCY SELL - Force sell all positions to go 100% CASH
// Run once: node emergencySell.js

require('dotenv').config();
const Binance = require('binance-api-node').default;

const client = Binance({
    apiKey: process.env.BINANCE_API_KEY,
    apiSecret: process.env.BINANCE_SECRET_KEY,
});

async function emergencySellAll() {
    console.log('\n🚨 EMERGENCY SELL - GOING TO 100% CASH\n');
    console.log('Reason: Holiday weekend + Bearish market + Iran/oil risk\n');
    
    try {
        // Get account balances
        const account = await client.accountInfo();
        const balances = account.balances.filter(b => 
            parseFloat(b.free) > 0 && 
            b.asset !== 'USDT' && 
            ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'AVAX', 'LINK', 'ADA', 'DOT', 'FET', 'NEAR'].includes(b.asset)
        );
        
        console.log('Positions to liquidate:');
        for (const balance of balances) {
            const qty = parseFloat(balance.free);
            if (qty > 0) {
                console.log(`  ${balance.asset}: ${qty}`);
            }
        }
        console.log('');
        
        // Sell each position
        for (const balance of balances) {
            const symbol = balance.asset + 'USDT';
            const qty = parseFloat(balance.free);
            
            if (qty <= 0) continue;
            
            try {
                // Get symbol info for precision
                const exchangeInfo = await client.exchangeInfo();
                const symbolInfo = exchangeInfo.symbols.find(s => s.symbol === symbol);
                
                if (!symbolInfo) {
                    console.log(`❌ Symbol ${symbol} not found`);
                    continue;
                }
                
                // Get lot size filter for precision
                const lotFilter = symbolInfo.filters.find(f => f.filterType === 'LOT_SIZE');
                const minQty = parseFloat(lotFilter.minQty);
                const stepSize = parseFloat(lotFilter.stepSize);
                
                // Calculate quantity with proper precision
                const precision = Math.max(0, -Math.floor(Math.log10(stepSize)));
                let sellQty = Math.floor(qty / stepSize) * stepSize;
                sellQty = parseFloat(sellQty.toFixed(precision));
                
                if (sellQty < minQty) {
                    console.log(`⚪ ${symbol}: Quantity ${qty} below minimum ${minQty} - skipping`);
                    continue;
                }
                
                // Get current price
                const ticker = await client.prices({ symbol });
                const price = parseFloat(ticker[symbol]);
                const value = sellQty * price;
                
                // Execute market sell
                console.log(`📤 SELLING ${sellQty} ${symbol} @ ~$${price.toFixed(4)} (~$${value.toFixed(2)})`);
                
                const order = await client.order({
                    symbol: symbol,
                    side: 'SELL',
                    type: 'MARKET',
                    quantity: sellQty.toString()
                });
                
                console.log(`   ✅ SOLD! Order ID: ${order.orderId}`);
                
            } catch (err) {
                console.log(`   ❌ Failed to sell ${symbol}: ${err.message}`);
            }
        }
        
        // Final balance check
        console.log('\n📊 Final Portfolio:');
        const finalAccount = await client.accountInfo();
        let total = 0;
        for (const b of finalAccount.balances) {
            const qty = parseFloat(b.free) + parseFloat(b.locked);
            if (qty > 0.0001) {
                if (b.asset === 'USDT') {
                    console.log(`   USDT: $${qty.toFixed(2)}`);
                    total += qty;
                } else {
                    try {
                        const ticker = await client.prices({ symbol: b.asset + 'USDT' });
                        const price = parseFloat(ticker[b.asset + 'USDT']);
                        const value = qty * price;
                        if (value > 0.01) {
                            console.log(`   ${b.asset}: ${qty.toFixed(4)} (~$${value.toFixed(2)})`);
                            total += value;
                        }
                    } catch (e) {}
                }
            }
        }
        console.log(`   💰 TOTAL: $${total.toFixed(2)} USDT`);
        console.log('\n✅ EMERGENCY SELL COMPLETE - You are now 100% CASH');
        console.log('   Wait until Monday for ETF/CME to reopen before trading again.');
        
    } catch (err) {
        console.error('Error:', err.message);
    }
}

emergencySellAll();
