#!/usr/bin/env python3
"""
Binance Trading Bot - Main Entry Point
=====================================
Automated cryptocurrency trading bot using technical indicators

Usage:
    python main.py              # Start in paper trading mode (default)
    python main.py --live       # Start in LIVE trading mode (real money!)
    python main.py --status     # Show current status
    python main.py --stats      # Show trading statistics
"""
import argparse
import logging
import sys
import os
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

import config
from bot.trader import Trader
from bot.database import Database

# Setup logging
def setup_logging():
    """Configure logging"""
    log_format = '%(asctime)s | %(levelname)-8s | %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # Create logs directory
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    # File handler
    log_file = log_dir / f"bot_{datetime.now().strftime('%Y%m%d')}.log"
    
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    
    # Reduce noise from external libraries
    logging.getLogger('ccxt').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def print_banner(mode: str):
    """Print startup banner"""
    banner = """
╔══════════════════════════════════════════════════════════════════╗
║                    BINANCE TRADING BOT                           ║
║                    ═══════════════════                           ║
║                                                                  ║
║  Strategy: RSI + MACD + EMA Crossover                           ║
║  Risk: Stop-Loss {sl}% | Take-Profit {tp}%                       ║
║  Mode: {mode}                                              ║
║                                                                  ║
║  ⚠️  TRADING CRYPTOCURRENCIES INVOLVES SIGNIFICANT RISK         ║
║      Only trade with money you can afford to lose               ║
╚══════════════════════════════════════════════════════════════════╝
""".format(
    sl=config.STOP_LOSS_PERCENT,
    tp=config.TAKE_PROFIT_PERCENT,
    mode=mode.upper().center(20)
)
    print(banner)


def check_api_keys() -> bool:
    """Verify API keys are configured"""
    if not config.BINANCE_API_KEY or not config.BINANCE_SECRET_KEY:
        print("\n❌ ERROR: Binance API keys not configured!")
        print("\nPlease create a .env file with:")
        print("  BINANCE_API_KEY=your_api_key")
        print("  BINANCE_SECRET_KEY=your_secret_key")
        print("\nOr set TRADING_MODE=paper in .env for paper trading without keys")
        return False
    return True


def show_status():
    """Show current bot status"""
    try:
        # Try paper mode first to avoid API key issues
        trader = Trader(paper_mode=True)
        status = trader.get_status()
        
        print("\n" + "=" * 50)
        print("BOT STATUS")
        print("=" * 50)
        print(f"Mode: {status['mode'].upper()}")
        print(f"Running: {'Yes' if status['running'] else 'No'}")
        print(f"Balance: ${status['balance']:.2f}")
        print(f"Starting Capital: ${status['starting_capital']:.2f}")
        print(f"Total Return: {status['total_return']:.2f}%")
        print(f"Daily P&L: ${status['daily_pnl']:.2f} ({status['daily_pnl_percent']:.2f}%)")
        
        if status['positions']:
            print("\nOpen Positions:")
            for pos in status['positions']:
                print(f"  {pos['symbol']}: {pos['amount']:.6f} @ ${pos['entry_price']:.4f} "
                      f"| Current: ${pos['current_price']:.4f} "
                      f"| P&L: ${pos['pnl']:.2f} ({pos['pnl_percent']:.2f}%)")
        else:
            print("\nNo open positions")
        
        print("=" * 50)
        
    except Exception as e:
        print(f"Error getting status: {e}")


def show_stats():
    """Show trading statistics"""
    try:
        db = Database()
        stats = db.get_stats(days=30)
        
        print("\n" + "=" * 50)
        print("TRADING STATISTICS (Last 30 Days)")
        print("=" * 50)
        print(f"Total Trades: {stats['total_trades']}")
        print(f"Winning Trades: {stats['winning_trades']}")
        print(f"Losing Trades: {stats['losing_trades']}")
        print(f"Win Rate: {stats['win_rate']:.1f}%")
        print(f"Total P&L: ${stats['total_pnl']:.2f}")
        print(f"Average P&L: ${stats['avg_pnl']:.2f} ({stats['avg_pnl_percent']:.2f}%)")
        print(f"Best Trade: ${stats['best_trade']:.2f}")
        print(f"Worst Trade: ${stats['worst_trade']:.2f}")
        print("=" * 50)
        
        # Show recent trades
        trades = db.get_trades(limit=10)
        if trades:
            print("\nRecent Trades:")
            for t in trades:
                pnl_str = f"${t['pnl_usdt']:.2f}" if t['pnl_usdt'] else "N/A"
                print(f"  {t['symbol']} | {t['side'].upper()} | "
                      f"Entry: ${t['entry_price']:.4f} | Exit: ${t['exit_price']:.4f} | "
                      f"P&L: {pnl_str} | {t['exit_reason'] or ''}")
        
    except Exception as e:
        print(f"Error getting stats: {e}")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Binance Trading Bot')
    parser.add_argument('--live', action='store_true', 
                       help='Run in LIVE mode with real money (DANGEROUS!)')
    parser.add_argument('--status', action='store_true',
                       help='Show current bot status')
    parser.add_argument('--stats', action='store_true',
                       help='Show trading statistics')
    parser.add_argument('--iterations', type=int, default=None,
                       help='Number of iterations to run (default: infinite)')
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    logger = logging.getLogger(__name__)
    
    # Handle info commands
    if args.status:
        show_status()
        return
    
    if args.stats:
        show_stats()
        return
    
    # Determine trading mode
    paper_mode = not args.live
    if not paper_mode and config.TRADING_MODE.lower() == 'paper':
        print("\n⚠️  WARNING: --live flag used but TRADING_MODE=paper in config")
        print("    Set TRADING_MODE=live in .env to enable live trading")
        paper_mode = True
    
    # Check API keys for live mode
    if not paper_mode:
        if not check_api_keys():
            sys.exit(1)
        
        # Extra confirmation for live trading
        print("\n" + "!" * 60)
        print("! WARNING: YOU ARE ABOUT TO START LIVE TRADING !")
        print("! This will use REAL MONEY from your Binance account !")
        print("!" * 60)
        
        confirm = input("\nType 'YES I UNDERSTAND' to continue: ")
        if confirm != "YES I UNDERSTAND":
            print("Aborted.")
            sys.exit(0)
    
    # Print banner
    mode_str = "PAPER TRADING" if paper_mode else "🔴 LIVE TRADING 🔴"
    print_banner(mode_str)
    
    # Start bot
    try:
        logger.info(f"Starting bot in {'PAPER' if paper_mode else 'LIVE'} mode")
        
        trader = Trader(paper_mode=paper_mode)
        trader.run(iterations=args.iterations)
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
