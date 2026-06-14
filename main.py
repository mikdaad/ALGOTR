"""
==================================================================================
  MAIN ORCHESTRATOR — v4 Live Breakout + Velocity Scanner (main.py)
==================================================================================
  
  Two operating modes:
  
    MODE 1: PRE-MARKET SCREENER
      python main.py --screener
      Runs the Phase 1 VCP & trend-aligned screener.
      Outputs a Top-15 watchlist to screened_watchlist.json.
      
    MODE 2: LIVE ENGINE (default)
      python main.py
      Reads screened_watchlist.json, subscribes WebSocket to those tokens,
      and runs:
        Phase 2: 5-min Opening Range Breakout + WOBI detection
        Phase 4: 1-min 3-Point Velocity Scalp Scanner (NEW)
      All triggered signals are printed in color and logged to CSV.
      
  DAILY WORKFLOW:
    Evening:    python auth.py             # Login for tomorrow
                python main.py --screener  # VCP + trend scan
    Morning:    python main.py             # Live breakout + velocity engine
    
  NO TRADES ARE EXECUTED. All signals are alert-only.
  Signals logged to: triggered_signals.csv
==================================================================================
"""

import sys
import os
import time
import argparse
import traceback
from datetime import datetime

import pytz

from config import (
    KITE_API_KEY,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    ENABLE_DB_LOGGING,
    OBI_BULL_THRESHOLD,
    OBI_BEAR_THRESHOLD,
    OPENING_RANGE_MINUTES,
    LIVE_CANDLE_MINUTES,
    VELOCITY_PRICE_MIN,
    VELOCITY_PRICE_MAX,
    VELOCITY_ATR_MIN,
    VELOCITY_WOBI_BULL,
    VELOCITY_WOBI_BEAR,
    VELOCITY_SCALP_TARGET,
    VELOCITY_STOP_LOSS,
    HITL_ENABLED,
)
from auth import load_access_token, get_authenticated_kite

IST = pytz.timezone("Asia/Kolkata")

# Enable ANSI color codes on Windows 10+ terminals
if os.name == "nt":
    os.system("")


# ──────────────────────────────────────────────────────────────────────────────
# MARKET HOURS HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def is_market_hours() -> bool:
    """Check if current time is within NSE trading hours."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return market_open <= now <= market_close


def wait_for_market_open():
    """Block until market opens. Handles weekends and pre-market."""
    from datetime import timedelta
    while True:
        now = datetime.now(IST)
        target = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)

        if now.weekday() >= 5 or now >= target.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE):
            target += timedelta(days=1)
            while target.weekday() >= 5:
                target += timedelta(days=1)
            target = target.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)

        if now < target:
            wait = (target - now).total_seconds()
            hrs = wait / 3600
            print(f"\n🌙 Market closed. Next open: {target.strftime('%a %d %b %H:%M IST')} ({hrs:.1f}h away)")
            print(f"   Sleeping... (Ctrl+C to exit)\n")
            # Sleep in 5-minute chunks for Ctrl+C responsiveness
            while datetime.now(IST) < target:
                chunk = min((target - datetime.now(IST)).total_seconds(), 300)
                if chunk > 0:
                    time.sleep(chunk)
        else:
            break


# ──────────────────────────────────────────────────────────────────────────────
# MODE 1: PRE-MARKET SCREENER
# ──────────────────────────────────────────────────────────────────────────────

def run_screener_mode():
    """Execute Phase 1 pre-market screener."""
    from screener import run_screener
    from db_logger import log_screener_result

    print_banner("SCREENER")

    kite = get_authenticated_kite()
    print("🔐 Authenticated.\n")

    results = run_screener(kite)

    if not results.empty and ENABLE_DB_LOGGING:
        log_screener_result(results)

    print("\n✅ Screener complete. Run 'python main.py' during market hours for live engine.")


# ──────────────────────────────────────────────────────────────────────────────
# MODE 2: LIVE BREAKOUT + OBI ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def run_live_mode():
    """Execute Phase 2+4+5 live breakout + VPA + velocity engine with HITL Execution Gateway."""
    from screener import load_screened_watchlist
    from live_engine import LiveBreakoutEngine
    from alerts import handle_breakout_signal, handle_velocity_signal, CSV_LOG_FILE
    from volume_profile import handle_vpa_signal

    print_banner("LIVE")

    # Load access token
    token_data = load_access_token()
    access_token = token_data["access_token"]
    print(f"🔐 Authenticated as: {token_data.get('user_id', 'N/A')}\n")

    # Load watchlist (from screener output or fallback)
    watchlist = load_screened_watchlist()

    if not watchlist:
        print("❌ No watchlist available. Run 'python main.py --screener' first.")
        sys.exit(1)

    # Display watchlist with token IDs for verification
    print(f"📋 Watchlist ({len(watchlist)} tokens):")
    for s in watchlist:
        trend_label = s.get('trend', 'N/A')
        print(f"   • {s['symbol']:<14} token={s['token']:<10} trend={trend_label}")
    print(f"\n📄 Signals will be logged to: {os.path.abspath(CSV_LOG_FILE)}\n")

    # Initialize HITL components if enabled
    bot = None
    listener = None
    if HITL_ENABLED:
        print("   🚀 HITL Mode is ENABLED. Booting Execution Gateway...")
        try:
            kite = get_authenticated_kite()
        except Exception as e:
            print(f"❌ Failed to get authenticated Kite Connect instance for order placement: {e}")
            sys.exit(1)
        
        from order_executor import OrderExecutor
        from hitl_bot import HITLBot
        from supabase_listener import SupabaseApprovalListener
        from alerts import register_hitl_bot

        executor = OrderExecutor(kite=kite)
        bot = HITLBot(execute_fn=executor.execute_signal)
        register_hitl_bot(bot)
        
        listener = SupabaseApprovalListener(execute_fn=executor.execute_signal)
        
        bot.start()
        listener.start()
    else:
        print("   ⚠️ HITL Mode is DISABLED. Operating in ALERT ONLY mode.")

    # Wait for market to open
    if not is_market_hours():
        wait_for_market_open()

    # Initialize and start the live engine
    engine = LiveBreakoutEngine(
        api_key=KITE_API_KEY,
        access_token=access_token,
        watchlist=watchlist,
        on_signal=handle_breakout_signal,
        on_velocity_signal=handle_velocity_signal,
        on_vpa_signal=handle_vpa_signal,   # Phase 5: VPA standalone callback signals
    )

    try:
        engine.start()
        print("\n🚀 Live engine running. Ctrl+C to stop.\n")

        # Keep alive + periodic status logging
        status_interval = 300  # Print status every 5 minutes
        while True:
            if not is_market_hours():
                print("\n🔔 Market closed for the day.")
                break
            engine.print_status()
            time.sleep(status_interval)

    except KeyboardInterrupt:
        print("\n\n🛑 Stopped by user (Ctrl+C).")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        traceback.print_exc()
    finally:
        engine.stop()
        if bot:
            try:
                bot.stop()
            except Exception as e:
                print(f"Error stopping Telegram bot: {e}")
        if listener:
            try:
                listener.stop()
            except Exception as e:
                print(f"Error stopping Supabase listener: {e}")
        print("   Goodbye!")


# ──────────────────────────────────────────────────────────────────────────────
# BANNER
# ──────────────────────────────────────────────────────────────────────────────

def print_banner(mode: str):
    """Print startup banner."""
    now = datetime.now(IST)
    print()
    print("=" * 70)
    if mode == "SCREENER":
        print("   ZERODHA SIGNAL ENGINE v5 -- VCP & TREND-ALIGNED SCREENER")
    else:
        print("   ZERODHA SIGNAL ENGINE v5 -- VPA BREAKOUT + TIER-WEIGHTED OBI")
    print("=" * 70)
    print(f"   Time:              {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    if mode == "LIVE":
        print(f"   Candle Interval:   {LIVE_CANDLE_MINUTES}min")
        print(f"   Opening Range:     First {OPENING_RANGE_MINUTES}min")
        print(f"   OBI Thresholds:    Bull ≥ {OBI_BULL_THRESHOLD} | Bear ≤ {OBI_BEAR_THRESHOLD}")
        from config import OBI_TIER_WEIGHTS
        print(f"   OBI Tier Weights:  {OBI_TIER_WEIGHTS}")
        print(f"   ──────────────────────────────────────────────────────────────────")
        print(f"   Velocity Scanner: ARMED")
        print(f"   Price Band:        Rs{VELOCITY_PRICE_MIN:.0f}-Rs{VELOCITY_PRICE_MAX:.0f}")
        print(f"   1m ATR Gate:       >= {VELOCITY_ATR_MIN} pts")
        print(f"   WOBI Velocity:     Bull >= {VELOCITY_WOBI_BULL} | Bear <= {VELOCITY_WOBI_BEAR}")
        print(f"   Scalp Target:      +-Rs{VELOCITY_SCALP_TARGET:.2f} (R:R = 1:2)")
        print(f"   Stop-Loss:         +-Rs{VELOCITY_STOP_LOSS:.2f}")
        print(f"   ------")
        print(f"   StreamingVolumeProfile: ARMED (bin=Rs0.10, 70% VA rule)")
        print(f"   VPA Setups:        VAH_BREAKOUT | VAL_BREAKDOWN | POC_REJECTION | OR_BREAKOUT")
    print(f"   DB Logging:        {'✅ Enabled' if ENABLE_DB_LOGGING else '❌ Disabled'}")
    print(f"   CSV Signal Log:    triggered_signals.csv")
    print(f"   Market Hours:      {MARKET_OPEN_HOUR}:{MARKET_OPEN_MINUTE:02d} – {MARKET_CLOSE_HOUR}:{MARKET_CLOSE_MINUTE:02d} IST")
    print("=" * 70)
    if HITL_ENABLED:
        print("   Mode: HITL EXECUTION GATEWAY — Orders placed on confirmation.")
    else:
        print("   Mode: ALERT ONLY — No auto-trading. Execute on Groww manually.")
    print("=" * 70)
    print()


# ──────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Zerodha Signal Engine v3 — VCP Screener + Live Breakout + Weighted OBI"
    )
    parser.add_argument(
        "--screener", "-s",
        action="store_true",
        help="Run Phase 1: Pre-Market Global Stock Screener"
    )
    args = parser.parse_args()

    try:
        if args.screener:
            run_screener_mode()
        else:
            run_live_mode()
    except FileNotFoundError as e:
        print(f"\n❌ {e}")
        print("   Run 'python auth.py' first to log in.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n🛑 Interrupted.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
