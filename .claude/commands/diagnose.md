# /diagnose — BinanceBot Loss Diagnostic

Run a full diagnostic on the bot's trade history and report what's working and what isn't.

## Steps

1. Run `python analyze_trades.py` from `/home/user/BinanceBot` and show the full output.

2. Check how many trades are in the database:
   ```
   python -c "
   import sqlite3; from pathlib import Path
   db = Path('data/trades.db')
   if not db.exists(): print('DB does not exist yet — bot has not run')
   else:
       c = sqlite3.connect(str(db))
       total = c.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
       closed = c.execute('SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL').fetchone()[0]
       open_n = total - closed
       print(f'Total: {total}  |  Closed: {closed}  |  Open: {open_n}')
       c.close()
   "
   ```

3. Check `DRY_RUN` status in the live bot:
   ```
   grep -n "DRY_RUN" python-bot/smart_trader_v3_live.py | head -3
   ```

4. Based on the analyze_trades.py output, summarize:
   - Whether there are enough trades for conclusions (need 30+)
   - The top 1–2 loss causes ranked by severity
   - One specific change that would most improve performance
   - How many more days of trading are needed before the next review

Keep the summary under 10 lines.
