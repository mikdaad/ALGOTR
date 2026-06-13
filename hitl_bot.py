"""
==================================================================================
  HITL TELEGRAM BOT — Interactive Approval Gateway (hitl_bot.py)
==================================================================================
  Interactive Telegram bot using python-telegram-bot v20+ (async).
  Sends velocity signals with InlineKeyboard APPROVE/REJECT buttons.

  CONVERSATION FLOW:
    1. Signal fires → Bot sends formatted message with ✅ APPROVE / ❌ REJECT
    2. User clicks ✅ APPROVE → Message edits to ask for "QTY SL TARGET"
    3. User types: "50 431.00 435.50"
    4. Bot shows ORDER CONFIRMATION with 🚀 EXECUTE / 🔙 CANCEL
    5. User clicks 🚀 EXECUTE → kite.place_order() → Message shows result

  THREADING:
    Runs python-telegram-bot's async Application in a dedicated daemon thread
    with its own asyncio event loop. The sync worker thread dispatches via
    asyncio.run_coroutine_threadsafe().

  SETUP:
    pip install python-telegram-bot>=20.0
    Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in config.py
==================================================================================
"""

import asyncio
import threading
import time
from datetime import datetime
from typing import Dict, Optional, Callable, Any

import pytz

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    HITL_EXPIRY_SECONDS,
    HITL_PRODUCT_TYPE,
)

IST = pytz.timezone("Asia/Kolkata")


class HITLBot:
    """
    Human-In-The-Loop Telegram Bot for velocity signal approval.

    Runs the async python-telegram-bot Application in a separate daemon
    thread. Signal dispatch from the sync worker is done via
    asyncio.run_coroutine_threadsafe().
    """

    def __init__(self, execute_fn: Optional[Callable] = None):
        """
        Args:
            execute_fn: Callable(row_id, qty, sl, target) -> dict
                        Returns {"success": bool, "order_id": str, "error": str}
        """
        self._execute_fn = execute_fn
        self._app = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # ── State tracking ──
        # Pending signals awaiting user decision: {row_id: signal_data_dict}
        self._pending_signals: Dict[int, dict] = {}
        # The row_id the user is currently typing params for (one at a time)
        self._active_config: Optional[int] = None
        # Params the user entered, awaiting final EXECUTE confirmation
        self._pending_confirmation: Optional[dict] = None

    # ──────────────────────────────────────────────────────────────────────
    # LIFECYCLE
    # ──────────────────────────────────────────────────────────────────────

    def start(self):
        """Start the bot polling loop in a daemon thread."""
        if "your_" in TELEGRAM_BOT_TOKEN:
            print("   🤖 HITL Bot: ⚠ Skipped (configure TELEGRAM_BOT_TOKEN in config.py)")
            return

        self._thread = threading.Thread(target=self._run_bot, daemon=True, name="hitl-bot")
        self._thread.start()

        # Wait for the event loop to be ready
        deadline = time.time() + 10
        while self._loop is None and time.time() < deadline:
            time.sleep(0.1)

        if self._loop:
            print("   🤖 HITL Bot: ✅ Telegram bot started (polling)")
        else:
            print("   🤖 HITL Bot: ❌ Failed to start within 10s")

    def _run_bot(self):
        """Internal: create event loop and run the Application."""
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            MessageHandler,
            filters,
        )

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._app = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .build()
        )

        # Register handlers
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text_input)
        )

        # Start the application and run polling
        self._loop.run_until_complete(self._app.initialize())
        self._loop.run_until_complete(self._app.start())
        self._loop.run_until_complete(
            self._app.updater.start_polling(drop_pending_updates=True)
        )
        self._loop.run_forever()

    def stop(self):
        """Gracefully stop the bot."""
        if self._loop and self._app:
            async def _shutdown():
                try:
                    await self._app.updater.stop()
                    await self._app.stop()
                    await self._app.shutdown()
                except Exception:
                    pass
            try:
                future = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
                future.result(timeout=10)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            print("   🤖 HITL Bot: 🛑 Stopped")

    # ──────────────────────────────────────────────────────────────────────
    # PUBLIC API (called from sync worker thread)
    # ──────────────────────────────────────────────────────────────────────

    def send_approval_request(self, signal_data: dict, row_id: int):
        """
        Thread-safe: dispatch an interactive approval message to Telegram.

        Args:
            signal_data: Normalized signal dict (from supabase_bridge).
            row_id: The Supabase trading_signals.id for this signal.
        """
        if self._loop is None:
            print("   🤖 HITL Bot: ❌ Bot not running, cannot send approval")
            return

        # Store for later reference
        self._pending_signals[row_id] = {
            **signal_data,
            "row_id": row_id,
            "_created_at": datetime.now(IST),
        }

        coro = self._send_approval_message(signal_data, row_id)
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ──────────────────────────────────────────────────────────────────────
    # TELEGRAM MESSAGE FORMATTERS
    # ──────────────────────────────────────────────────────────────────────

    async def _send_approval_message(self, s: dict, row_id: int):
        """Send the initial signal message with APPROVE/REJECT buttons."""
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton

        icon = "⚡" if s.get("direction") == "BUY" else "💥"

        text = (
            f"{icon} *3\\-POINT VELOCITY — {s['direction']} {_esc(s['symbol'])}*\n"
            f"\n"
            f"📌 Signal: `{_esc(s.get('signal_type', 'N/A'))}`\n"
            f"💰 Entry: `₹{s['trigger_price']:.2f}`\n"
            f"🎯 Recommended Target: `₹{s.get('target_price', 0):.2f}`\n"
            f"🛑 Recommended Stop\\-Loss: `₹{s['stop_loss']:.2f}`\n"
            f"📊 WOBI: `{s.get('wobi_ratio', 0):+.4f}`\n"
            f"📈 ATR\\(1m\\): `{s.get('atr_1m', 0):.4f} pts`\n"
            f"🔊 Volume: `{s.get('volume_spike', 0):.2f}× avg`\n"
            f"🕐 Time: `{_esc(str(s.get('signal_time', 'N/A')))}`\n"
            f"\n"
            f"_Signal ID: {row_id} · Expires in {HITL_EXPIRY_SECONDS}s_"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ APPROVE", callback_data=f"approve:{row_id}"),
                InlineKeyboardButton("❌ REJECT", callback_data=f"reject:{row_id}"),
            ]
        ])

        try:
            await self._app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=keyboard,
            )
        except Exception as e:
            print(f"   🤖 HITL Bot: ❌ Failed to send approval: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # CALLBACK QUERY HANDLER (button presses)
    # ──────────────────────────────────────────────────────────────────────

    async def _handle_callback(self, update, context):
        """Route all inline button presses."""
        query = update.callback_query
        await query.answer()  # Dismiss Telegram loading spinner

        data = query.data or ""

        if data.startswith("approve:"):
            row_id = int(data.split(":")[1])
            await self._on_approve(query, row_id)
        elif data.startswith("reject:"):
            row_id = int(data.split(":")[1])
            await self._on_reject(query, row_id)
        elif data.startswith("execute:"):
            row_id = int(data.split(":")[1])
            await self._on_execute(query, row_id)
        elif data.startswith("cancel:"):
            row_id = int(data.split(":")[1])
            await self._on_cancel(query, row_id)

    async def _on_approve(self, query, row_id: int):
        """User clicked ✅ APPROVE — ask for trade parameters."""
        signal = self._pending_signals.get(row_id)
        if not signal:
            await query.edit_message_text("❌ Signal not found or already processed.")
            return

        # Check expiry
        age = (datetime.now(IST) - signal["_created_at"]).total_seconds()
        if age > HITL_EXPIRY_SECONDS:
            await query.edit_message_text(
                f"⏰ *Signal Expired*\n\n"
                f"{signal['symbol']} {signal['direction']} — "
                f"expired after {HITL_EXPIRY_SECONDS}s\\.\n"
                f"Signal was {age:.0f}s old\\.",
                parse_mode="MarkdownV2",
            )
            self._pending_signals.pop(row_id, None)
            _update_status_safe(row_id, "expired")
            return

        # Set this as the active configuration session
        self._active_config = row_id

        sl = signal.get("stop_loss", 0)
        tgt = signal.get("target_price", 0)

        await query.edit_message_text(
            f"✅ *APPROVED — {_esc(signal['symbol'])} {signal['direction']}*\n\n"
            f"Reply with your trade parameters:\n"
            f"  `QTY  STOP_LOSS  TARGET`\n\n"
            f"Example: `50 {sl:.2f} {tgt:.2f}`\n"
            f"_\\(Recommended: SL\\=₹{sl:.2f}, Target\\=₹{tgt:.2f}\\)_",
            parse_mode="MarkdownV2",
        )

        _update_status_safe(row_id, "approved", approved_via="telegram")

    async def _on_reject(self, query, row_id: int):
        """User clicked ❌ REJECT."""
        signal = self._pending_signals.pop(row_id, None)
        sym = signal["symbol"] if signal else "Unknown"
        d = signal["direction"] if signal else "?"

        await query.edit_message_text(
            f"❌ *REJECTED — {_esc(sym)} {d}*\n\n"
            f"Signal ID: {row_id}\n"
            f"No order was placed\\.",
            parse_mode="MarkdownV2",
        )

        _update_status_safe(row_id, "rejected", approved_via="telegram")

    # ──────────────────────────────────────────────────────────────────────
    # TEXT INPUT HANDLER (user types QTY SL TARGET)
    # ──────────────────────────────────────────────────────────────────────

    async def _handle_text_input(self, update, context):
        """Handle text messages — parse trade parameters."""
        if self._active_config is None:
            return  # No active config session, ignore

        row_id = self._active_config
        signal = self._pending_signals.get(row_id)
        if not signal:
            await update.message.reply_text("❌ No active signal to configure.")
            self._active_config = None
            return

        text = update.message.text.strip()
        parts = text.split()

        if len(parts) != 3:
            sl = signal.get("stop_loss", 0)
            tgt = signal.get("target_price", 0)
            await update.message.reply_text(
                f"❌ Send exactly 3 values: `QTY STOP_LOSS TARGET`\n"
                f"Example: `50 {sl:.2f} {tgt:.2f}`",
                parse_mode="Markdown",
            )
            return

        try:
            qty = int(parts[0])
            sl = float(parts[1])
            target = float(parts[2])
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid numbers. QTY must be integer, SL and TARGET must be decimal.",
            )
            return

        if qty <= 0:
            await update.message.reply_text("❌ Quantity must be positive.")
            return

        # Store confirmation params
        self._pending_confirmation = {
            "row_id": row_id,
            "qty": qty,
            "sl": sl,
            "target": target,
        }
        self._active_config = None

        # Calculate risk/reward
        entry = signal.get("trigger_price", 0)
        risk_per_share = abs(entry - sl)
        reward_per_share = abs(target - entry)
        risk_total = risk_per_share * qty
        reward_total = reward_per_share * qty
        rr = reward_per_share / risk_per_share if risk_per_share > 0 else 0

        from telegram import InlineKeyboardMarkup, InlineKeyboardButton

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚀 EXECUTE", callback_data=f"execute:{row_id}"),
                InlineKeyboardButton("🔙 CANCEL", callback_data=f"cancel:{row_id}"),
            ]
        ])

        await update.message.reply_text(
            f"📋 *ORDER CONFIRMATION — {signal['symbol']}*\n\n"
            f"Direction: `{signal['direction']}`\n"
            f"Quantity: `{qty} shares`\n"
            f"Entry: `₹{entry:.2f}` \\({HITL_PRODUCT_TYPE}\\)\n"
            f"Stop\\-Loss: `₹{sl:.2f}`\n"
            f"Target: `₹{target:.2f}`\n\n"
            f"Risk: `₹{risk_total:.2f}` \\| Reward: `₹{reward_total:.2f}` \\| R:R: `1:{rr:.1f}`\n\n"
            f"_Signal ID: {row_id}_",
            parse_mode="MarkdownV2",
            reply_markup=keyboard,
        )

    # ──────────────────────────────────────────────────────────────────────
    # EXECUTE / CANCEL HANDLERS
    # ──────────────────────────────────────────────────────────────────────

    async def _on_execute(self, query, row_id: int):
        """User clicked 🚀 EXECUTE — place the order via Kite."""
        conf = self._pending_confirmation
        if not conf or conf["row_id"] != row_id:
            await query.edit_message_text("❌ No pending confirmation for this signal.")
            return

        signal = self._pending_signals.pop(row_id, None)
        if not signal:
            await query.edit_message_text("❌ Signal data not found.")
            return

        self._pending_confirmation = None

        # Call the executor (sync function, run in thread executor)
        if self._execute_fn:
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: self._execute_fn(
                        row_id=row_id,
                        qty=conf["qty"],
                        sl=conf["sl"],
                        target=conf["target"],
                    ),
                )
            except Exception as e:
                result = {"success": False, "order_id": None, "error": str(e)}
        else:
            result = {"success": False, "order_id": None, "error": "No executor configured"}

        if result.get("success"):
            oid = result.get("order_id", "N/A")
            await query.edit_message_text(
                f"✅ *ORDER EXECUTED — {_esc(signal['symbol'])} {signal['direction']}*\n\n"
                f"Order ID: `{_esc(str(oid))}`\n"
                f"Qty: `{conf['qty']}` @ `₹{signal['trigger_price']:.2f}` LIMIT\n"
                f"SL: `₹{conf['sl']:.2f}` \\| Target: `₹{conf['target']:.2f}`\n"
                f"Product: `{HITL_PRODUCT_TYPE}`\n\n"
                f"_Monitor on Kite/Groww\\._",
                parse_mode="MarkdownV2",
            )
        else:
            error = _esc(result.get("error", "Unknown error"))
            await query.edit_message_text(
                f"❌ *ORDER FAILED — {_esc(signal['symbol'])} {signal['direction']}*\n\n"
                f"Error: `{error}`\n\n"
                f"_No order was placed\\._",
                parse_mode="MarkdownV2",
            )

    async def _on_cancel(self, query, row_id: int):
        """User clicked 🔙 CANCEL — dismiss without ordering."""
        signal = self._pending_signals.pop(row_id, None)
        self._pending_confirmation = None
        sym = _esc(signal["symbol"]) if signal else "Unknown"

        await query.edit_message_text(
            f"🔙 *CANCELLED — {sym}*\n\n"
            f"Order not placed\\. Signal dismissed\\.",
            parse_mode="MarkdownV2",
        )

        _update_status_safe(row_id, "rejected", approved_via="telegram")


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    result = []
    for ch in str(text):
        if ch in special:
            result.append(f"\\{ch}")
        else:
            result.append(ch)
    return "".join(result)


def _update_status_safe(row_id: int, status: str, **kwargs):
    """Update Supabase signal status, swallowing errors."""
    try:
        from supabase_bridge import update_signal_status
        update_signal_status(row_id, status, **kwargs)
    except Exception as e:
        print(f"   🤖 HITL Bot: ⚠ Status update failed for {row_id}: {e}")
