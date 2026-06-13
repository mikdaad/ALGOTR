"""
==================================================================================
  ORDER EXECUTOR — Kite place_order() Wrapper (order_executor.py)
==================================================================================
  Thread-safe execution gateway that places live orders on Zerodha via
  kite.place_order(). Uses atomic Supabase status transitions to prevent
  duplicate orders from race conditions between Telegram and dashboard
  approval channels.

  SAFETY FEATURES:
    - Atomic status gate: UPDATE WHERE status IN ('pending','approved')
      → if 0 rows affected, the signal was already processed.
    - Thread lock around execution to serialize concurrent calls.
    - Full error capture → written to Supabase for audit trail.
    - Tagged orders: all HITL orders carry tag="HITL_VEL" for Kite Console filtering.

  ARCHITECTURE:
    hitl_bot.py / supabase_listener.py
      → executor.execute_signal(row_id, qty, sl, target)
      → kite.place_order()
      → supabase_bridge.update_signal_status()
==================================================================================
"""

import threading
from datetime import datetime
from typing import Optional, Dict, Any

import pytz

from config import HITL_PRODUCT_TYPE, HITL_ORDER_TYPE

IST = pytz.timezone("Asia/Kolkata")


class OrderExecutor:
    """
    Safe order execution wrapper around kite.place_order().
    Uses atomic Supabase status transitions to prevent duplicate orders.
    """

    def __init__(self, kite=None):
        """
        Args:
            kite: Authenticated KiteConnect instance. Can be set later via set_kite().
        """
        self._kite = kite
        self._lock = threading.Lock()

    def set_kite(self, kite):
        """Set or replace the authenticated Kite client."""
        self._kite = kite

    def execute_signal(
        self,
        row_id: int,
        qty: int,
        sl: float,
        target: float,
    ) -> Dict[str, Any]:
        """
        Execute a trading signal. Thread-safe.

        Args:
            row_id:  Supabase trading_signals.id
            qty:     Number of shares to trade
            sl:      User-specified stop-loss price
            target:  User-specified target price

        Returns:
            {"success": bool, "order_id": str | None, "error": str | None}
        """
        with self._lock:
            return self._do_execute(row_id, qty, sl, target)

    def _do_execute(
        self, row_id: int, qty: int, sl: float, target: float,
    ) -> Dict[str, Any]:
        """Internal execution logic — called under the lock."""
        from supabase_bridge import update_signal_status, get_signal_by_id

        # ── 1. Read signal from Supabase ──
        signal = get_signal_by_id(row_id)
        if not signal:
            return {"success": False, "order_id": None,
                    "error": f"Signal {row_id} not found in database"}

        # ── 2. Atomic status gate: pending/approved → executing ──
        # If this returns 0, someone else already processed the signal.
        rows_updated = update_signal_status(
            row_id,
            "executing",
            condition_status=["pending", "approved"],
            quantity=qty,
        )
        if not rows_updated:
            current = signal.get("status", "unknown")
            return {"success": False, "order_id": None,
                    "error": f"Signal already processed (status={current})"}

        # ── 3. Guard: Kite client must be available ──
        if not self._kite:
            update_signal_status(
                row_id, "failed",
                execution_error="Kite client not initialized",
            )
            return {"success": False, "order_id": None,
                    "error": "Kite client not initialized"}

        # ── 4. Place the order ──
        try:
            symbol = signal["symbol"]
            direction = signal["direction"]
            entry = float(signal["trigger_price"])

            # Map config strings to Kite constants
            if HITL_PRODUCT_TYPE == "MIS":
                product = self._kite.PRODUCT_MIS
            else:
                product = self._kite.PRODUCT_CNC

            if HITL_ORDER_TYPE == "LIMIT":
                order_type = self._kite.ORDER_TYPE_LIMIT
            else:
                order_type = self._kite.ORDER_TYPE_MARKET

            order_id = self._kite.place_order(
                variety=self._kite.VARIETY_REGULAR,
                exchange=self._kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=direction,       # "BUY" or "SELL"
                quantity=qty,
                product=product,
                order_type=order_type,
                price=entry if order_type == self._kite.ORDER_TYPE_LIMIT else None,
                validity=self._kite.VALIDITY_DAY,
                tag="HITL_VEL",
            )

            # ── 5. Success → update status ──
            update_signal_status(
                row_id,
                "executed",
                order_id=str(order_id),
                quantity=qty,
                stop_loss=sl,
                target_price=target,
            )

            print(
                f"   🚀 HITL: ✅ Order placed — "
                f"{direction} {qty}× {symbol} @ ₹{entry:.2f} "
                f"(ID: {order_id})"
            )
            return {"success": True, "order_id": str(order_id), "error": None}

        except Exception as e:
            error_msg = str(e)
            update_signal_status(row_id, "failed", execution_error=error_msg)
            print(f"   🚀 HITL: ❌ Order failed — {error_msg}")
            return {"success": False, "order_id": None, "error": error_msg}
