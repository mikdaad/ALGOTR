"""
==================================================================================
  PHASE 3+5 — Alerting, Logging & HITL Execution Gateway (alerts.py) [v4]
==================================================================================
  Handles all signal notifications:
    1. ANSI color-coded console alerts with WOBI metrics
    2. Telegram bot message with full diagnostics
    3. CSV file logging to triggered_signals.csv
    4. Database logging (optional, via db_logger)
    5. Supabase dashboard push (returns row_id for HITL tracking)
    6. HITL interactive Telegram approval (velocity signals only)

  Breakout signals remain ALERT-ONLY.
  Velocity signals are routed through the HITL gateway when enabled.
==================================================================================
"""

import csv
import os
import requests
from datetime import datetime
from typing import Union

import pytz

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    HITL_ENABLED,
    VELOCITY_SCALP_TARGET,
    VELOCITY_STOP_LOSS,
)

IST = pytz.timezone("Asia/Kolkata")
CSV_LOG_FILE = "triggered_signals.csv"

# ANSI escape codes for Windows Terminal / modern consoles
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_MAGENTA = "\033[95m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_BLINK = "\033[5m"
_BG_CYAN = "\033[46m"
_BG_BLACK = "\033[40m"
_RESET = "\033[0m"


# ──────────────────────────────────────────────────────────────────────────────
# CSV SIGNAL LOGGER
# ──────────────────────────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "Timestamp", "Symbol", "Direction", "Breakout_Type", "Trigger_Price",
    "Stop_Loss", "WOBI_Ratio", "Bid_Qty", "Ask_Qty", "OR_High", "OR_Low",
    "Candle_OHLC", "Trend", "Token",
]


def _ensure_csv_header():
    """Create the CSV file with headers if it doesn't exist."""
    if not os.path.exists(CSV_LOG_FILE):
        with open(CSV_LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)


def log_signal_to_csv(signal) -> None:
    """
    Append a triggered signal to triggered_signals.csv.
    Thread-safe: opens file in append mode per write (no shared handle).
    """
    _ensure_csv_header()

    ohlc = f"{signal.candle_open}/{signal.candle_high}/{signal.candle_low}/{signal.candle_close}"
    trend = getattr(signal, "trend", "N/A")

    row = [
        signal.timestamp.strftime("%Y-%m-%d %H:%M:%S") if hasattr(signal.timestamp, "strftime")
            else str(signal.timestamp),
        signal.symbol,
        signal.direction,
        signal.breakout_type,
        signal.entry_price,
        signal.stop_loss,
        f"{signal.obi:+.4f}",
        signal.total_bid_qty,
        signal.total_ask_qty,
        signal.or_high,
        signal.or_low,
        ohlc,
        trend,
        signal.token,
    ]

    try:
        with open(CSV_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)
        print(f"   📄 CSV: Logged to {CSV_LOG_FILE}")
    except Exception as e:
        print(f"   📄 CSV: ❌ Write failed — {e}")


# ──────────────────────────────────────────────────────────────────────────────
# CSV VELOCITY SIGNAL LOGGER
# ──────────────────────────────────────────────────────────────────────────────

VELOCITY_CSV_LOG_FILE = "triggered_signals.csv"

VELOCITY_CSV_COLUMNS = [
    "Timestamp", "Symbol", "Direction", "Signal_Type", "Trigger_Price",
    "Target_Price", "Stop_Loss", "WOBI_Ratio", "ATR_1m", "Volume_Ratio",
    "Current_Vol", "Avg_Vol", "Consol_High", "Consol_Low",
    "Bid_Qty", "Ask_Qty", "Trend", "Token",
]


def _ensure_velocity_csv_header():
    """Create the velocity CSV file with headers if it doesn't exist."""
    if not os.path.exists(VELOCITY_CSV_LOG_FILE):
        with open(VELOCITY_CSV_LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(VELOCITY_CSV_COLUMNS)


def log_velocity_signal_to_csv(signal) -> None:
    """
    Append a velocity scalp signal to triggered_signals.csv.
    Thread-safe: opens file in append mode per write (no shared handle).
    """
    _ensure_velocity_csv_header()

    row = [
        signal.timestamp.strftime("%Y-%m-%d %H:%M:%S") if hasattr(signal.timestamp, "strftime")
            else str(signal.timestamp),
        signal.symbol,
        signal.direction,
        signal.signal_type,
        signal.trigger_price,
        signal.target_price,
        signal.stop_loss,
        f"{signal.wobi:+.4f}",
        f"{signal.atr_1m:.4f}",
        f"{signal.volume_ratio:.2f}x",
        signal.current_volume,
        int(signal.avg_volume),
        signal.consolidation_high,
        signal.consolidation_low,
        signal.total_bid_qty,
        signal.total_ask_qty,
        signal.trend,
        signal.token,
    ]

    try:
        with open(VELOCITY_CSV_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)
        print(f"   📄 CSV: Velocity signal logged to {VELOCITY_CSV_LOG_FILE}")
    except Exception as e:
        print(f"   📄 CSV: ❌ Velocity write failed — {e}")


# ──────────────────────────────────────────────────────────────────────────────
# CONSOLE ALERTS — ANSI Color-Coded Breakout + WOBI Signals
# ──────────────────────────────────────────────────────────────────────────────

def print_breakout_signal(signal) -> None:
    """Print a highly visible, color-coded console alert for a breakout signal."""
    is_buy = signal.direction == "BUY"
    color = _GREEN if is_buy else _RED
    icon = "🟢" if is_buy else "🔴"
    dir_label = f"{color}{_BOLD}{signal.direction}{_RESET}"
    obi_bar = _obi_bar(signal.obi)
    trend = getattr(signal, "trend", "N/A")
    border = "═" * 68

    print()
    print(f"  {color}╔{border}╗{_RESET}")
    print(f"  {color}║{_RESET}  {icon}  {_BOLD}STRONG BREAKOUT ALERT — {dir_label}  {icon}".ljust(90) + f"{color}║{_RESET}")
    print(f"  {color}╠{border}╣{_RESET}")
    print(f"  {color}║{_RESET}  Ticker:         {_BOLD}{_CYAN}{signal.symbol}{_RESET}".ljust(90) + f"{color}║{_RESET}")
    print(f"  {color}║{_RESET}  Breakout Type:  {signal.breakout_type}".ljust(82) + f"{color}║{_RESET}")
    print(f"  {color}║{_RESET}  Entry Trigger:  {_BOLD}₹{signal.entry_price}{_RESET}".ljust(90) + f"{color}║{_RESET}")
    print(f"  {color}║{_RESET}  Stop-Loss:      {_YELLOW}₹{signal.stop_loss}{_RESET}".ljust(90) + f"{color}║{_RESET}")
    print(f"  {color}║{_RESET}  WOBI Ratio:     {_BOLD}{signal.obi:+.4f}{_RESET}  {obi_bar}".ljust(90) + f"{color}║{_RESET}")
    print(f"  {color}║{_RESET}  Bid Qty:        {signal.total_bid_qty:,}".ljust(82) + f"{color}║{_RESET}")
    print(f"  {color}║{_RESET}  Ask Qty:        {signal.total_ask_qty:,}".ljust(82) + f"{color}║{_RESET}")
    print(f"  {color}║{_RESET}  Trend (EMA20):  {trend}".ljust(82) + f"{color}║{_RESET}")
    print(f"  {color}╠{border}╣{_RESET}")
    print(f"  {color}║{_RESET}  Opening Range:  High=₹{signal.or_high}  Low=₹{signal.or_low}".ljust(82) + f"{color}║{_RESET}")
    print(f"  {color}║{_RESET}  Candle OHLC:    O=₹{signal.candle_open} H=₹{signal.candle_high} L=₹{signal.candle_low} C=₹{signal.candle_close}".ljust(82) + f"{color}║{_RESET}")
    print(f"  {color}║{_RESET}  Time:           {signal.timestamp}".ljust(82) + f"{color}║{_RESET}")
    print(f"  {color}╠{border}╣{_RESET}")
    print(f"  {color}║{_RESET}  {_YELLOW}⚡ Execute manually on Groww — NO auto-trade ⚡{_RESET}".ljust(90) + f"{color}║{_RESET}")
    print(f"  {color}╚{border}╝{_RESET}")
    print()


def print_velocity_signal(signal) -> None:
    """
    Print a high-impact, flashing cyan ANSI console alert for a velocity scalp.
    Distinct visual profile from the standard breakout alerts.
    """
    is_buy = signal.direction == "BUY"
    color = _GREEN if is_buy else _RED
    icon = "⚡" if is_buy else "💥"
    dir_label = f"{color}{_BOLD}{signal.direction}{_RESET}"
    obi_bar = _obi_bar(signal.wobi)
    border = "═" * 72

    # Risk-Reward visual
    risk = abs(signal.trigger_price - signal.stop_loss)
    reward = abs(signal.target_price - signal.trigger_price)
    rr_label = f"1:{reward / risk:.1f}" if risk > 0 else "N/A"

    print()
    print(f"  {_BG_CYAN}{_BOLD} {'':^72} {_RESET}")
    print(f"  {_CYAN}╔{border}╗{_RESET}")
    print(f"  {_CYAN}║{_RESET}  {icon}  {_BLINK}{_CYAN}{_BOLD}3-POINT {signal.direction} VELOCITY ALERT{_RESET}  {icon}".ljust(100) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}╠{border}╣{_RESET}")
    print(f"  {_CYAN}║{_RESET}  Ticker:           {_BOLD}{_CYAN}{signal.symbol}{_RESET}".ljust(94) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}║{_RESET}  Signal:           {_BOLD}{signal.signal_type}{_RESET}".ljust(94) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}╠{border}╣{_RESET}")
    print(f"  {_CYAN}║{_RESET}  {_BOLD}Entry Trigger:{_RESET}     {color}{_BOLD}₹{signal.trigger_price}{_RESET}".ljust(94) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}║{_RESET}  {_GREEN}Scalp Target:{_RESET}      {_GREEN}{_BOLD}₹{signal.target_price}{_RESET}  (+₹{VELOCITY_SCALP_TARGET:.2f})".ljust(94) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}║{_RESET}  {_RED}Stop-Loss:{_RESET}         {_RED}{_BOLD}₹{signal.stop_loss}{_RESET}  (-₹{VELOCITY_STOP_LOSS:.2f})".ljust(94) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}║{_RESET}  Risk:Reward:      {_BOLD}{rr_label}{_RESET}".ljust(94) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}╠{border}╣{_RESET}")
    print(f"  {_CYAN}║{_RESET}  WOBI Ratio:       {_BOLD}{signal.wobi:+.4f}{_RESET}  {obi_bar}".ljust(94) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}║{_RESET}  1m ATR(20):       {signal.atr_1m:.4f} pts".ljust(86) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}║{_RESET}  Volume Surge:     {_BOLD}{signal.volume_ratio:.2f}× avg{_RESET}  (curr={signal.current_volume:,} / avg={int(signal.avg_volume):,})".ljust(94) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}║{_RESET}  Bid Qty:          {signal.total_bid_qty:,}".ljust(86) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}║{_RESET}  Ask Qty:          {signal.total_ask_qty:,}".ljust(86) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}║{_RESET}  Trend (EMA20):    {signal.trend}".ljust(86) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}╠{border}╣{_RESET}")
    print(f"  {_CYAN}║{_RESET}  Consol Range:     High=₹{signal.consolidation_high}  Low=₹{signal.consolidation_low}".ljust(86) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}║{_RESET}  Time:             {signal.timestamp}".ljust(86) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}╠{border}╣{_RESET}")
    print(f"  {_CYAN}║{_RESET}  {_YELLOW}{_BOLD}⚡ SCALP — Execute manually on Groww — NO auto-trade ⚡{_RESET}".ljust(94) + f"{_CYAN}║{_RESET}")
    print(f"  {_CYAN}╚{border}╝{_RESET}")
    print(f"  {_BG_CYAN}{_BOLD} {'':^72} {_RESET}")
    print()


def _obi_bar(obi: float) -> str:
    """Visual bar for WOBI ratio with color."""
    filled = int(abs(obi) * 10)
    if obi >= 0:
        bar = "█" * filled + "░" * (10 - filled)
        return f"{_GREEN}[{bar}]{_RESET} BUYERS"
    else:
        bar = "█" * filled + "░" * (10 - filled)
        return f"SELLERS {_RED}[{bar}]{_RESET}"


def print_no_signals() -> None:
    """Print brief status when no signals are found."""
    print(f"   {_DIM}✅ No breakout + WOBI setups detected this cycle.{_RESET}")


# ──────────────────────────────────────────────────────────────────────────────
# TELEGRAM ALERTS
# ──────────────────────────────────────────────────────────────────────────────

def format_breakout_telegram(signal) -> str:
    """Format a BreakoutSignal into a Telegram message."""
    icon = "🟢" if signal.direction == "BUY" else "🔴"
    obi_label = "BUYERS DOMINANT" if signal.obi > 0 else "SELLERS DOMINANT"
    trend = getattr(signal, "trend", "N/A")

    msg = (
        f"{icon} *STRONG {signal.direction} — {signal.symbol}*\n"
        f"\n"
        f"📌 Breakout: `{signal.breakout_type}`\n"
        f"💰 Entry: `₹{signal.entry_price}`\n"
        f"🛑 Stop-Loss: `₹{signal.stop_loss}`\n"
        f"📊 WOBI: `{signal.obi:+.4f}` ({obi_label})\n"
        f"📈 Bid: `{signal.total_bid_qty:,}` / Ask: `{signal.total_ask_qty:,}`\n"
        f"📐 OR: `High=₹{signal.or_high} Low=₹{signal.or_low}`\n"
        f"📉 Trend: `{trend}`\n"
        f"🕐 Time: `{signal.timestamp}`\n"
        f"📈 OHLC: `{signal.candle_open}/{signal.candle_high}/{signal.candle_low}/{signal.candle_close}`\n"
        f"\n"
        f"⚡ _Execute manually on Groww_"
    )
    return msg


def send_telegram(signal) -> bool:
    """
    Send a breakout alert via Telegram bot.
    Returns True on success, False otherwise.
    Silently skips if credentials are not configured.
    """
    if "your_" in TELEGRAM_BOT_TOKEN or "your_" in TELEGRAM_CHAT_ID:
        print(f"   {_DIM}📱 Telegram: Skipped (configure in config.py){_RESET}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    if hasattr(signal, "obi"):
        text = format_breakout_telegram(signal)
    else:
        text = _format_legacy_telegram(signal)

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            sym = getattr(signal, "symbol", "?")
            print(f"   📱 Telegram: ✅ Sent for {sym}")
            return True
        else:
            print(f"   📱 Telegram: ❌ HTTP {resp.status_code}")
            return False
    except requests.exceptions.Timeout:
        print("   📱 Telegram: ❌ Timeout")
        return False
    except requests.exceptions.ConnectionError:
        print("   📱 Telegram: ❌ Connection error")
        return False
    except Exception as e:
        print(f"   📱 Telegram: ❌ {e}")
        return False


def _format_legacy_telegram(signal) -> str:
    """Backward-compatible formatter for old Signal dataclass."""
    icon = "🟢" if signal.direction == "BUY" else "🔴"
    return (
        f"{icon} *{signal.direction} — {signal.symbol}*\n"
        f"Pattern: `{signal.pattern}`\n"
        f"Entry: `₹{signal.entry_price}` | SL: `₹{signal.stop_loss}`\n"
        f"Volume: `{signal.volume:,}` ({signal.volume_ratio}× avg)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# UNIFIED ALERT HANDLERS
# ──────────────────────────────────────────────────────────────────────────────

def handle_breakout_signal(signal) -> None:
    """
    Full alert pipeline for a single BreakoutSignal.
    Called as the on_signal callback from LiveBreakoutEngine.

    Pipeline: Console → CSV → Telegram → Database → Dashboard
    """
    # 1. Color-coded console alert
    print_breakout_signal(signal)

    # 2. CSV file logging (always on, thread-safe)
    log_signal_to_csv(signal)

    # 3. Telegram alert
    send_telegram(signal)

    # 4. Database logging (import lazily to avoid circular deps)
    try:
        from db_logger import log_breakout_signal
        log_breakout_signal(signal)
    except ImportError:
        pass
    except Exception as e:
        print(f"   💾 DB: ❌ {e}")

    # 5. Push to Supabase for live dashboard
    try:
        from supabase_bridge import push_breakout_to_dashboard
        push_breakout_to_dashboard(signal)
    except ImportError:
        pass
    except Exception as e:
        print(f"   🌐 Dashboard: ❌ {e}")


# ──────────────────────────────────────────────────────────────────────────────
# HITL BOT REGISTRATION
# ──────────────────────────────────────────────────────────────────────────────
# The HITLBot instance is registered here from main.py so the velocity
# pipeline can dispatch interactive approval requests.

_hitl_bot = None


def register_hitl_bot(bot) -> None:
    """Register the HITLBot instance for velocity signal approvals."""
    global _hitl_bot
    _hitl_bot = bot
    print("   🤖 HITL Bot: Registered with alert pipeline")


def handle_velocity_signal(signal) -> None:
    """
    Full alert pipeline for a VelocitySignal (3-point scalp).
    Called as the on_velocity_signal callback from VelocityScanner.

    Pipeline: Console → CSV → Telegram alert → Database → Dashboard → HITL

    When HITL_ENABLED:
      Steps 1-4 run as before (console, CSV, legacy Telegram, DB).
      Step 5 pushes to Supabase and captures the row_id.
      Step 6 sends an INTERACTIVE Telegram message with APPROVE/REJECT buttons.
      The legacy Telegram alert (step 3) still fires as a notification.
    """
    # 1. High-impact flashing cyan console alert
    print_velocity_signal(signal)

    # 2. CSV file logging (velocity-specific columns)
    log_velocity_signal_to_csv(signal)

    # 3. Telegram alert (legacy — notification only, no buttons)
    _send_velocity_telegram(signal)

    # 4. Database logging
    try:
        from db_logger import log_breakout_signal
        log_breakout_signal(signal)
    except ImportError:
        pass
    except Exception as e:
        print(f"   💾 DB: ❌ {e}")

    # 5. Push to Supabase for live dashboard (returns row_id for HITL)
    row_id = None
    try:
        from supabase_bridge import push_velocity_to_dashboard
        success, row_id = push_velocity_to_dashboard(signal)
    except ImportError:
        pass
    except Exception as e:
        print(f"   🌐 Dashboard: ❌ {e}")

    # 6. HITL: Send interactive Telegram approval request
    if HITL_ENABLED and _hitl_bot and row_id:
        try:
            # Build signal data dict for the bot
            signal_data = {
                "symbol": signal.symbol,
                "direction": signal.direction,
                "signal_type": signal.signal_type,
                "trigger_price": float(signal.trigger_price),
                "target_price": float(signal.target_price),
                "stop_loss": float(signal.stop_loss),
                "wobi_ratio": float(signal.wobi),
                "atr_1m": float(signal.atr_1m),
                "volume_spike": float(signal.volume_ratio),
                "signal_time": str(signal.timestamp),
            }
            _hitl_bot.send_approval_request(signal_data, row_id)
            print(f"   🤖 HITL: ✅ Approval request sent for {signal.symbol} (ID: {row_id})")
        except Exception as e:
            print(f"   🤖 HITL: ❌ {e}")
    elif HITL_ENABLED and not _hitl_bot:
        print(f"   🤖 HITL: ⚠ Bot not registered — signal not gated")


def _send_velocity_telegram(signal) -> bool:
    """Send a velocity scalp alert via Telegram bot."""
    if "your_" in TELEGRAM_BOT_TOKEN or "your_" in TELEGRAM_CHAT_ID:
        print(f"   {_DIM}📱 Telegram: Skipped (configure in config.py){_RESET}")
        return False

    icon = "⚡" if signal.direction == "BUY" else "💥"
    risk = abs(signal.trigger_price - signal.stop_loss)
    reward = abs(signal.target_price - signal.trigger_price)
    rr_label = f"1:{reward / risk:.1f}" if risk > 0 else "N/A"

    msg = (
        f"{icon} *3-POINT {signal.direction} VELOCITY — {signal.symbol}*\n"
        f"\n"
        f"🎯 Signal: `{signal.signal_type}`\n"
        f"💰 Entry: `₹{signal.trigger_price}`\n"
        f"✅ Target: `₹{signal.target_price}` (+₹3.00)\n"
        f"🛑 Stop: `₹{signal.stop_loss}` (-₹1.50)\n"
        f"📐 R:R: `{rr_label}`\n"
        f"📊 WOBI: `{signal.wobi:+.4f}`\n"
        f"📈 ATR(1m): `{signal.atr_1m:.4f} pts`\n"
        f"🔊 Volume: `{signal.volume_ratio:.2f}× avg`\n"
        f"📉 Trend: `{signal.trend}`\n"
        f"🕐 Time: `{signal.timestamp}`\n"
        f"\n"
        f"⚡ _SCALP — Execute manually on Groww_"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"   📱 Telegram: ✅ Velocity alert sent for {signal.symbol}")
            return True
        else:
            print(f"   📱 Telegram: ❌ HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"   📱 Telegram: ❌ {e}")
        return False
