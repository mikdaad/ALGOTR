"""
==================================================================================
  SUPABASE APPROVAL LISTENER — Dashboard Web Approvals (supabase_listener.py)
==================================================================================
  Polls the Supabase `trading_signals` table for rows where the status has
  been updated to 'approved' via the Next.js dashboard. When found, triggers
  the OrderExecutor to place the order.

  ARCHITECTURE:
    Next.js Dashboard → Supabase UPDATE (status='approved', qty, sl, target)
      → This listener polls every 2s
      → OrderExecutor.execute_signal()
      → kite.place_order()

  NOTE: Uses polling (not Supabase Realtime) because the Python supabase-py
  client's realtime support is less mature than the JS client. A 2-second
  poll interval is perfectly adequate for HITL latency requirements.
==================================================================================
"""

import threading
import time
from typing import Callable, Optional

from config import HITL_ENABLED


class SupabaseApprovalListener:
    """
    Polls Supabase for dashboard-approved signals and triggers execution.
    Runs in a daemon thread.
    """

    def __init__(
        self,
        execute_fn: Callable,
        poll_interval: float = 2.0,
    ):
        """
        Args:
            execute_fn: Callable(row_id, qty, sl, target) -> dict
            poll_interval: Seconds between polls (default 2.0)
        """
        self._execute_fn = execute_fn
        self._poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        """Start the polling loop in a daemon thread."""
        if not HITL_ENABLED:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="supabase-listener",
        )
        self._thread.start()
        print("   🌐 Listener: ✅ Polling for dashboard approvals")

    def stop(self):
        """Stop the polling loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("   🌐 Listener: 🛑 Stopped")

    def _poll_loop(self):
        """Main polling loop — runs in daemon thread."""
        from supabase_bridge import _get_client

        while self._running:
            try:
                client = _get_client()

                # Find signals approved via dashboard (not yet executing/executed)
                result = (
                    client.table("trading_signals")
                    .select("id, symbol, direction, trigger_price, stop_loss, target_price, quantity")
                    .eq("status", "approved")
                    .eq("approved_via", "dashboard")
                    .execute()
                )

                for signal in (result.data or []):
                    row_id = signal["id"]
                    qty = signal.get("quantity") or 1
                    sl = float(signal.get("stop_loss", 0))
                    target = float(signal.get("target_price") or signal["trigger_price"])

                    print(
                        f"   🌐 Listener: 📥 Dashboard approval detected — "
                        f"{signal['direction']} {signal['symbol']} (ID: {row_id})"
                    )

                    try:
                        self._execute_fn(
                            row_id=row_id,
                            qty=qty,
                            sl=sl,
                            target=target,
                        )
                    except Exception as e:
                        print(f"   🌐 Listener: ❌ Execution failed for {row_id}: {e}")

            except Exception as e:
                # Don't crash the thread on transient errors
                print(f"   🌐 Listener: ⚠ Poll error: {e}")

            time.sleep(self._poll_interval)
