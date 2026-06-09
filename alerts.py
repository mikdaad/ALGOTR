"""
==================================================================================
  PHASE 3 — Decoupled Alerting & Signal Logging (alerts.py) [v3]
==================================================================================
  Handles all signal notifications:
    1. ANSI color-coded console alerts with WOBI metrics
    2. Telegram bot message with full diagnostics
    3. CSV file logging to triggered_signals.csv
    4. Database logging (optional, via db_logger)

  NO TRADES ARE EXECUTED. This is alert-only.
==================================================================================
"""

import csv
import os
import requests
from datetime import datetime
from typing import Union

import pytz

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

IST = pytz.timezone("Asia/Kolkata")
CSV_LOG_FILE = "triggered_signals.csv"

# ANSI escape codes for Windows Terminal / modern consoles
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
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
# UNIFIED ALERT HANDLER
# ──────────────────────────────────────────────────────────────────────────────

def handle_breakout_signal(signal) -> None:
    """
    Full alert pipeline for a single BreakoutSignal.
    Called as the on_signal callback from LiveBreakoutEngine.

    Pipeline: Console → CSV → Telegram → Database
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
