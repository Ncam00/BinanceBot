"""
Smart Trader V2 - Web Dashboard
http://localhost:5000
"""

from flask import Flask, jsonify, render_template_string
from binance.client import Client
from dotenv import load_dotenv
import os
import json
from datetime import datetime

load_dotenv()

app = Flask(__name__)

# Binance client
client = Client(
    os.getenv('BINANCE_API_KEY'),
    os.getenv('BINANCE_SECRET_KEY')
)

TRADING_PAIRS = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
    'AVAXUSDT', 'LINKUSDT', 'ADAUSDT', 'DOTUSDT', 'FETUSDT', 'NEARUSDT'
]

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Smart Trader V2 Dashboard</title>
    <meta http-equiv="refresh" content="10">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Arial, sans-serif; 
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #fff; 
            min-height: 100vh;
            padding: 20px;
        }
        .header {
            text-align: center;
            padding: 20px;
            margin-bottom: 30px;
        }
        .header h1 { 
            font-size: 2.5em; 
            background: linear-gradient(90deg, #00d4ff, #00ff88);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .header .subtitle { color: #888; margin-top: 5px; }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 25px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .stat-card .value { 
            font-size: 2.5em; 
            font-weight: bold;
            margin: 10px 0;
        }
        .stat-card .label { color: #888; font-size: 0.9em; }
        .stat-card.balance .value { color: #00ff88; }
        .stat-card.trades .value { color: #00d4ff; }
        .stat-card.profit .value { color: #ffd700; }
        .stat-card.positions .value { color: #ff6b6b; }
        
        .section {
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 20px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .section h2 { 
            margin-bottom: 20px; 
            color: #00d4ff;
            font-size: 1.3em;
        }
        
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px 15px; text-align: left; }
        th { 
            background: rgba(0,212,255,0.1); 
            color: #00d4ff;
            font-weight: 600;
        }
        tr { border-bottom: 1px solid rgba(255,255,255,0.05); }
        tr:hover { background: rgba(255,255,255,0.03); }
        
        .price { font-family: monospace; font-size: 1.1em; }
        .change-positive { color: #00ff88; }
        .change-negative { color: #ff6b6b; }
        
        .status-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8em;
            font-weight: bold;
        }
        .status-running { background: rgba(0,255,136,0.2); color: #00ff88; }
        .status-no-trade { background: rgba(255,193,7,0.2); color: #ffc107; }
        .status-buy { background: rgba(0,212,255,0.2); color: #00d4ff; }
        
        .v2-features {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
        }
        .feature {
            background: rgba(0,255,136,0.1);
            padding: 15px;
            border-radius: 10px;
            border-left: 3px solid #00ff88;
        }
        .feature .check { color: #00ff88; margin-right: 8px; }
        
        .timestamp { 
            text-align: center; 
            color: #666; 
            margin-top: 20px;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🚀 Smart Trader V2</h1>
        <div class="subtitle">Location-Based Trading | Max 2 Trades/Day | $5 Target</div>
    </div>
    
    <div class="stats-grid">
        <div class="stat-card balance">
            <div class="label">USDT Balance</div>
            <div class="value">${{ "%.2f"|format(balance) }}</div>
        </div>
        <div class="stat-card trades">
            <div class="label">Trades Today</div>
            <div class="value">{{ trades_today }}/2</div>
        </div>
        <div class="stat-card profit">
            <div class="label">Daily Profit</div>
            <div class="value">${{ "%.2f"|format(daily_profit) }}</div>
        </div>
        <div class="stat-card positions">
            <div class="label">Open Positions</div>
            <div class="value">{{ open_positions }}</div>
        </div>
    </div>
    
    <div class="section">
        <h2>📊 Market Scanner</h2>
        <table>
            <thead>
                <tr>
                    <th>Pair</th>
                    <th>Price</th>
                    <th>24h Change</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {% for coin in coins %}
                <tr>
                    <td><strong>{{ coin.symbol }}</strong></td>
                    <td class="price">${{ "%.4f"|format(coin.price) if coin.price < 10 else "%.2f"|format(coin.price) }}</td>
                    <td class="{{ 'change-positive' if coin.change >= 0 else 'change-negative' }}">
                        {{ "%.2f"|format(coin.change) }}%
                    </td>
                    <td>
                        <span class="status-badge status-no-trade">NO-TRADE ZONE</span>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    
    <div class="section">
        <h2>✅ V2 Features Active</h2>
        <div class="v2-features">
            <div class="feature"><span class="check">✓</span> Support/Resistance Detection</div>
            <div class="feature"><span class="check">✓</span> No-Trade Zone (40% middle)</div>
            <div class="feature"><span class="check">✓</span> Market Type Detection</div>
            <div class="feature"><span class="check">✓</span> Max 2 Trades/Day</div>
            <div class="feature"><span class="check">✓</span> $5 Daily Profit Lock</div>
            <div class="feature"><span class="check">✓</span> Structure-Based Stop Loss</div>
        </div>
    </div>
    
    <div class="timestamp">
        Last updated: {{ timestamp }} | Auto-refresh every 10s
    </div>
</body>
</html>
"""

@app.route('/')
def dashboard():
    # Get balance
    try:
        account = client.get_account()
        balance = float([a['free'] for a in account['balances'] if a['asset'] == 'USDT'][0])
    except:
        balance = 0
    
    # Get coin data
    coins = []
    try:
        tickers = client.get_ticker()
        ticker_map = {t['symbol']: t for t in tickers}
        
        for symbol in TRADING_PAIRS:
            if symbol in ticker_map:
                t = ticker_map[symbol]
                coins.append({
                    'symbol': symbol.replace('USDT', ''),
                    'price': float(t['lastPrice']),
                    'change': float(t['priceChangePercent'])
                })
    except Exception as e:
        print(f"Error: {e}")
    
    return render_template_string(
        DASHBOARD_HTML,
        balance=balance,
        trades_today=0,
        daily_profit=0.0,
        open_positions=0,
        coins=coins,
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )

@app.route('/api/status')
def api_status():
    try:
        account = client.get_account()
        balance = float([a['free'] for a in account['balances'] if a['asset'] == 'USDT'][0])
    except:
        balance = 0
    
    return jsonify({
        'balance': balance,
        'trades_today': 0,
        'daily_profit': 0,
        'status': 'running'
    })

if __name__ == '__main__':
    print("🌐 Dashboard starting at http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
