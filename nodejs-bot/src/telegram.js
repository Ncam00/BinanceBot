/**
 * Telegram Alert Module
 * =====================
 * Sends daily P&L summaries and trade alerts
 */

const https = require('https');

class TelegramBot {
    constructor(botToken, chatId) {
        this.botToken = botToken;
        this.chatId = chatId;
        this.enabled = !!(botToken && chatId);
        
        if (this.enabled) {
            console.log('  ✓ Telegram alerts enabled');
        } else {
            console.log('  ⚠️ Telegram alerts disabled (missing token or chat ID)');
        }
    }
    
    /**
     * Send a message via Telegram Bot API
     */
    sendMessage(text) {
        if (!this.enabled) return Promise.resolve();
        
        return new Promise((resolve, reject) => {
            const data = JSON.stringify({
                chat_id: this.chatId,
                text,
                parse_mode: 'HTML'
            });
            
            const options = {
                hostname: 'api.telegram.org',
                path: `/bot${this.botToken}/sendMessage`,
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(data)
                }
            };
            
            const req = https.request(options, (res) => {
                let body = '';
                res.on('data', chunk => body += chunk);
                res.on('end', () => resolve(body));
            });
            
            req.on('error', (err) => {
                console.log(`  ⚠️ Telegram send failed: ${err.message}`);
                resolve(); // Don't crash bot on alert failure
            });
            
            req.setTimeout(10000, () => {
                req.destroy();
                resolve();
            });
            
            req.write(data);
            req.end();
        });
    }
    
    /**
     * Send trade alert (buy/sell)
     */
    async tradeAlert(type, symbol, price, amount, pnl = null) {
        const emoji = type === 'BUY' ? '🟢' : (pnl && pnl > 0 ? '💰' : '🔴');
        let msg = `${emoji} <b>${type}</b> ${symbol}\n`;
        msg += `Price: $${price.toFixed(4)}\n`;
        msg += `Amount: ${amount.toFixed(6)}`;
        
        if (pnl !== null) {
            const pnlEmoji = pnl >= 0 ? '✅' : '❌';
            msg += `\n${pnlEmoji} P&L: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
        }
        
        await this.sendMessage(msg);
    }
    
    /**
     * Send daily P&L summary
     */
    async dailySummary(data) {
        const { dailyPnL, dailyTrades, wins, losses, portfolio, openPositions } = data;
        const emoji = dailyPnL >= 0 ? '📈' : '📉';
        const sign = dailyPnL >= 0 ? '+' : '';
        
        let msg = `${emoji} <b>Daily Summary</b>\n`;
        msg += `━━━━━━━━━━━━━━━━━━━━\n`;
        msg += `P&L: <b>${sign}$${dailyPnL.toFixed(2)}</b>\n`;
        msg += `Trades: ${dailyTrades} (${wins}W / ${losses}L)\n`;
        msg += `Portfolio: $${portfolio.toFixed(2)}\n`;
        msg += `Open Positions: ${openPositions}`;
        
        if (dailyPnL > 0) {
            msg += `\n\n✅ Good day!`;
        } else if (dailyPnL === 0 && dailyTrades === 0) {
            msg += `\n\n⏸ No trades (market unfavorable)`;
        } else {
            msg += `\n\n⚠️ Loss day — guards active`;
        }
        
        await this.sendMessage(msg);
    }
    
    /**
     * Send startup notification
     */
    async startupAlert(portfolio) {
        const msg = `🚀 <b>Bot Started</b>\nPortfolio: $${portfolio.toFixed(2)}\nMode: LIVE`;
        await this.sendMessage(msg);
    }
    
    /**
     * Send stop-loss alert
     */
    async stopLossAlert(symbol, pnl, reason) {
        const msg = `🛑 <b>STOP LOSS</b> ${symbol}\nLoss: $${pnl.toFixed(2)}\nReason: ${reason}`;
        await this.sendMessage(msg);
    }
}

module.exports = TelegramBot;
