# 📱 Remote Monitoring Guide

## Quick Access (Local Network)

If your phone is on the same WiFi as your PC:
1. Find your PC's IP address: Open PowerShell and run `ipconfig`
2. Look for "IPv4 Address" (e.g., `192.168.1.100`)
3. On your phone browser, go to: `http://192.168.1.100:3000`

## Remote Access (From Anywhere)

### Option 1: ngrok (Easiest - Free)

1. Download ngrok: https://ngrok.com/download
2. Sign up for free account
3. Run in terminal:
   ```powershell
   cd c:\BinanceBot\nodejs-bot
   .\ngrok http 3000
   ```
4. You'll get a URL like: `https://abc123.ngrok.io`
5. Access this URL from ANY device!

### Option 2: Tailscale (More Secure - Free)

1. Download Tailscale: https://tailscale.com/download
2. Install on PC and phone
3. Log in with same account
4. Access via Tailscale IP: `http://100.x.x.x:3000`

### Option 3: Port Forwarding (Advanced)

1. Log into your router (usually 192.168.1.1)
2. Find "Port Forwarding" settings
3. Forward external port 3000 to your PC's internal IP
4. Access via your public IP: `http://YOUR_PUBLIC_IP:3000`
   (Find public IP at: https://whatismyipaddress.com)

⚠️ **Security Warning**: Port forwarding exposes your bot to the internet!

## Mobile Dashboard Features

The dashboard is mobile-responsive:
- ✅ Auto-refreshes every 5 seconds
- ✅ Shows balance, P&L, positions
- ✅ Pause/Resume controls
- ✅ Recent trades history

## API Endpoints for Custom Apps

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Bot status (running/paused) |
| `/api/balance` | GET | Current USDT balance |
| `/api/portfolio` | GET | Full portfolio with all assets |
| `/api/positions` | GET | Open positions |
| `/api/trades` | GET | Recent trades |
| `/api/profit` | GET | Profit tracking info |
| `/api/pause` | POST | Pause trading |
| `/api/resume` | POST | Resume trading |
| `/api/stop` | POST | Stop bot |

## Webhook Notifications (Coming Soon)

For Telegram/Discord notifications, add to `.env`:
```
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```
