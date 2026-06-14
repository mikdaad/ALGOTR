"""
==================================================================================
  SUPABASE REAL-TIME BRIDGE — Dashboard Signal Pusher (supabase_bridge.py)
==================================================================================
  Pushes trading signals to the Supabase `trading_signals` table the instant
  they are generated. This is the bridge between the local Python HFT engine
  and the remote Next.js live dashboard.

  v2 UPGRADES (HITL Gateway):
    - push_velocity_to_dashboard() now returns (success, row_id) for HITL tracking
    - update_signal_status() — atomic conditional status transitions
    - get_signal_by_id() — fetch a single signal for the executor

  SETUP:
    pip install supabase
    Set SUPABASE_URL and SUPABASE_SERVICE_KEY in config.py

  ARCHITECTURE:
    Signal fires → alerts.py pipeline → push_signal_to_dashboard()
    → Supabase INSERT (status='pending') → Realtime broadcast → Next.js
    → HITL Bot sends approval → User approves → Executor reads signal
    → Atomic status gate → kite.place_order()
==================================================================================
"""

import threading
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

import pytz

from config import (
    ENABLE_DB_LOGGING,
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
)

IST = pytz.timezone("Asia/Kolkata")

# ──────────────────────────────────────────────────────────────────────────────
# SUPABASE CLIENT (singleton, thread-safe initialization)
# ──────────────────────────────────────────────────────────────────────────────

_supabase_client = None
_client_lock = threading.Lock()


def _get_client():
    """Lazy-initialize the Supabase client (singleton)."""
    global _supabase_client
    if _supabase_client is None:
        with _client_lock:
            if _supabase_client is None:
                from supabase import create_client
                _supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _supabase_client


# ──────────────────────────────────────────────────────────────────────────────
# SIGNAL NORMALIZATION — Convert any signal type to unified dashboard schema
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_breakout_signal(signal) -> dict:
    """
    Normalize a BreakoutSignal into the trading_signals schema.

    v5 VPA upgrade: includes current_poc, current_vah, current_val,
    target_price, and vpa_signal_type so the Next.js dashboard can display
    institutional liquidity nodes alongside the signal entry/stop levels.
    """
    return {
        "signal_time": signal.timestamp.isoformat() if hasattr(signal.timestamp, "isoformat")
                       else str(signal.timestamp),
        "symbol": signal.symbol,
        "direction": signal.direction,
        "signal_type": signal.breakout_type,
        "trigger_price": float(signal.entry_price),
        "target_price": float(getattr(signal, "target_price", 0.0)) or None,
        "stop_loss": float(signal.stop_loss),
        "wobi_ratio": float(signal.obi),
        "volume_spike": None,
        "atr_1m": None,
        "total_bid_qty": signal.total_bid_qty,
        "total_ask_qty": signal.total_ask_qty,
        "trend": getattr(signal, "trend", "N/A"),
        "or_high": float(signal.or_high) if signal.or_high else None,
        "or_low": float(signal.or_low) if signal.or_low else None,
        # ── VPA v5 columns (must exist in Supabase trading_signals table) ──
        "current_poc": float(getattr(signal, "current_poc", 0.0)) or None,
        "current_vah": float(getattr(signal, "current_vah", 0.0)) or None,
        "current_val": float(getattr(signal, "current_val", 0.0)) or None,
        "vpa_signal_type": getattr(signal, "vpa_signal_type", "") or None,
        "status": "alert_only",
        "metadata": {
            "candle_open": float(signal.candle_open),
            "candle_high": float(signal.candle_high),
            "candle_low": float(signal.candle_low),
            "candle_close": float(signal.candle_close),
            "vpa_ready": getattr(signal, "vpa_ready", False),
            "token": signal.token,
        },
    }


def _normalize_velocity_signal(signal) -> dict:
    """
    Normalize a VelocitySignal into the trading_signals schema.

    v5 VPA upgrade: VPA levels are optionally included if the velocity
    scanner enriches its signals with profile data in a future update.
    Currently defaults to None for backward compatibility.
    """
    return {
        "signal_time": signal.timestamp.isoformat() if hasattr(signal.timestamp, "isoformat")
                       else str(signal.timestamp),
        "symbol": signal.symbol,
        "direction": signal.direction,
        "signal_type": signal.signal_type,
        "trigger_price": float(signal.trigger_price),
        "target_price": float(signal.target_price),
        "stop_loss": float(signal.stop_loss),
        "wobi_ratio": float(signal.wobi),
        "volume_spike": float(signal.volume_ratio),
        "atr_1m": float(signal.atr_1m),
        "total_bid_qty": signal.total_bid_qty,
        "total_ask_qty": signal.total_ask_qty,
        "trend": getattr(signal, "trend", "N/A"),
        "or_high": None,
        "or_low": None,
        # ── VPA v5 columns: velocity signals don't carry VPA data yet ──
        "current_poc": float(getattr(signal, "current_poc", 0.0)) or None,
        "current_vah": float(getattr(signal, "current_vah", 0.0)) or None,
        "current_val": float(getattr(signal, "current_val", 0.0)) or None,
        "vpa_signal_type": getattr(signal, "vpa_signal_type", "") or None,
        "status": "pending",
        "metadata": {
            "consolidation_high": float(signal.consolidation_high),
            "consolidation_low": float(signal.consolidation_low),
            "current_volume": signal.current_volume,
            "avg_volume": float(signal.avg_volume),
            "token": signal.token,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC API — Signal Push (INSERT)
# ──────────────────────────────────────────────────────────────────────────────

def push_breakout_to_dashboard(signal) -> bool:
    """
    Push a BreakoutSignal to the Supabase `trading_signals` table.
    Breakout signals are always alert-only (no HITL).
    Returns True on success, False on failure.
    """
    if not ENABLE_DB_LOGGING:
        return False

    try:
        client = _get_client()
        row = _normalize_breakout_signal(signal)
        client.table("trading_signals").insert(row).execute()
        print(f"   🌐 Dashboard: ✅ Pushed {signal.direction} {signal.symbol}")
        return True
    except Exception as e:
        print(f"   🌐 Dashboard: ❌ {e}")
        return False


def push_velocity_to_dashboard(signal) -> Tuple[bool, Optional[int]]:
    """
    Push a VelocitySignal to the Supabase `trading_signals` table.
    Returns (success, row_id) — row_id is needed for HITL approval tracking.

    The row is inserted with status='pending' so the HITL bot can reference it.
    """
    if not ENABLE_DB_LOGGING:
        return False, None

    try:
        client = _get_client()
        row = _normalize_velocity_signal(signal)
        result = client.table("trading_signals").insert(row).execute()

        row_id = None
        if result.data and len(result.data) > 0:
            row_id = result.data[0].get("id")

        print(f"   🌐 Dashboard: ✅ Pushed velocity {signal.direction} {signal.symbol} (ID: {row_id})")
        return True, row_id
    except Exception as e:
        print(f"   🌐 Dashboard: ❌ {e}")
        return False, None


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC API — Signal Status Updates (for HITL Gateway)
# ──────────────────────────────────────────────────────────────────────────────

def get_signal_by_id(row_id: int) -> Optional[dict]:
    """
    Fetch a single trading signal by its ID.
    Returns the row dict or None if not found.
    """
    try:
        client = _get_client()
        result = (
            client.table("trading_signals")
            .select("*")
            .eq("id", row_id)
            .limit(1)
            .execute()
        )
        if result.data and len(result.data) > 0:
            return result.data[0]
        return None
    except Exception as e:
        print(f"   🌐 Bridge: ❌ get_signal_by_id({row_id}): {e}")
        return None


def update_signal_status(
    row_id: int,
    status: str,
    condition_status: Optional[List[str]] = None,
    **kwargs,
) -> bool:
    """
    Update the status of a trading signal.

    Args:
        row_id: The signal ID to update.
        status: New status value ('approved', 'executing', 'executed', 'rejected', 'failed', 'expired').
        condition_status: If provided, only update if current status is in this list.
                          This enables atomic status transitions (prevents races).
        **kwargs: Additional columns to update (e.g., order_id, approved_via,
                  execution_error, quantity, stop_loss, target_price).

    Returns:
        True if the update affected a row, False otherwise.
    """
    try:
        client = _get_client()

        update_data: Dict[str, Any] = {"status": status}

        # Timestamps
        if status == "approved":
            from datetime import datetime
            update_data["approved_at"] = datetime.now(IST).isoformat()
        elif status in ("executed", "failed"):
            from datetime import datetime
            update_data["executed_at"] = datetime.now(IST).isoformat()

        # Merge any extra columns
        for key, value in kwargs.items():
            update_data[key] = value

        query = client.table("trading_signals").update(update_data).eq("id", row_id)

        # Atomic conditional update
        if condition_status:
            query = query.in_("status", condition_status)

        result = query.execute()

        affected = len(result.data) if result.data else 0
        if affected > 0:
            print(f"   🌐 Bridge: ✅ Signal {row_id} → status='{status}'")
            return True
        else:
            print(f"   🌐 Bridge: ⚠ Signal {row_id} not updated (condition not met)")
            return False

    except Exception as e:
        print(f"   🌐 Bridge: ❌ update_signal_status({row_id}, {status}): {e}")
        return False
