"""
==================================================================================
  VELOCITY SCANNER — 3-Point Intraday Scalp Detector (velocity_scanner.py) [v4]
==================================================================================
  Ultra-low-latency real-time scanner for rapid ±3 point price expansions.
  Designed to run inside the LiveBreakoutEngine's _worker_loop() thread.

  ARCHITECTURE:
    Tick → 1-min MicroCandle buffer (deque) → Rolling ATR/Volume/HL tracking
    → Velocity Trigger evaluation per tick → VelocitySignal emission

  TRIGGER CONDITIONS (BUY):
    1. LTP crosses ABOVE the max-high of the last 3 completed 1-min candles
    2. Current 1-min volume > 2× average volume of the last 10 candles
    3. WOBI ≥ +0.70 (aggressive buyers lifting the offer)

  TRIGGER CONDITIONS (SELL):
    1. LTP crosses BELOW the min-low of the last 3 completed 1-min candles
    2. Current 1-min volume > 2× average volume of the last 10 candles
    3. WOBI ≤ -0.70 (aggressive sellers hitting the bid)

  UNIVERSE FILTER (3-Point Feasibility):
    - Price band: ₹150–₹500 (3 pts = 0.6%–2.0% move)
    - 1-min ATR(20) ≥ 0.50 points (sufficient intraday velocity)

  SCALP PROJECTIONS:
    - Target: Entry ± 3.00 points
    - Stop-Loss: Entry ∓ 1.50 points → 1:2 Risk:Reward
==================================================================================
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, Set

import numpy as np
import pytz

from config import (
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    VELOCITY_PRICE_MIN,
    VELOCITY_PRICE_MAX,
    VELOCITY_ATR_PERIOD,
    VELOCITY_ATR_MIN,
    VELOCITY_CONSOLIDATION_BARS,
    VELOCITY_VOLUME_LOOKBACK,
    VELOCITY_VOLUME_MULTIPLIER,
    VELOCITY_WOBI_BULL,
    VELOCITY_WOBI_BEAR,
    VELOCITY_SCALP_TARGET,
    VELOCITY_STOP_LOSS,
)

IST = pytz.timezone("Asia/Kolkata")

# ──────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class MicroCandle:
    """1-minute micro-candle built from live ticks."""
    open: float = 0.0
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    volume: int = 0
    tick_count: int = 0
    boundary: Optional[datetime] = None

    def update(self, price: float, vol: int):
        if self.tick_count == 0:
            self.open = price
            self.high = price
            self.low = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume = vol
        self.tick_count += 1

    def is_valid(self) -> bool:
        return self.tick_count > 0 and self.low < float("inf")


@dataclass
class VelocitySignal:
    """Emitted when a 3-point velocity trigger fires."""
    symbol: str
    token: int
    direction: str                    # "BUY" or "SELL"
    signal_type: str                  # "3PT_VELOCITY_BUY" or "3PT_VELOCITY_SELL"
    trigger_price: float              # LTP at the moment of trigger
    target_price: float               # trigger ± 3.00
    stop_loss: float                  # trigger ∓ 1.50
    wobi: float                       # Weighted OBI at trigger
    atr_1m: float                     # Current 1-min ATR(20)
    volume_ratio: float               # Current bar vol / avg(10 bars) ratio
    consolidation_high: float         # Max high of last 3 bars
    consolidation_low: float          # Min low of last 3 bars
    current_volume: int               # Current 1-min bar volume
    avg_volume: float                 # Avg volume of last 10 bars
    total_bid_qty: int = 0
    total_ask_qty: int = 0
    trend: str = "N/A"
    timestamp: datetime = field(default_factory=lambda: datetime.now(IST))


# ──────────────────────────────────────────────────────────────────────────────
# PER-TOKEN STATE TRACKER
# ──────────────────────────────────────────────────────────────────────────────

class TokenVelocityState:
    """
    In-memory rolling state for a single instrument's 1-minute bars.
    Uses collections.deque for O(1) append/pop — zero allocation churn.
    """

    __slots__ = (
        "candle_history", "current_candle", "current_boundary",
        "_atr_buffer", "_volume_buffer", "_price_eligible",
        "_last_ltp",
    )

    def __init__(self, max_history: int = 25):
        # Rolling completed 1-min candle history (keeps last ~25 for ATR(20) + headroom)
        self.candle_history: deque = deque(maxlen=max_history)
        self.current_candle: MicroCandle = MicroCandle()
        self.current_boundary: Optional[datetime] = None

        # Pre-allocated numpy buffers — reused every computation to avoid GC pressure
        self._atr_buffer: np.ndarray = np.zeros(VELOCITY_ATR_PERIOD, dtype=np.float64)
        self._volume_buffer: np.ndarray = np.zeros(VELOCITY_VOLUME_LOOKBACK, dtype=np.float64)

        # Cache: has the stock been deemed eligible by price band?
        self._price_eligible: Optional[bool] = None
        self._last_ltp: float = 0.0

    def reset_eligibility(self):
        """Force re-check of price eligibility on next tick."""
        self._price_eligible = None


# ──────────────────────────────────────────────────────────────────────────────
# VELOCITY SCANNER ENGINE
# ──────────────────────────────────────────────────────────────────────────────

class VelocityScanner:
    """
    Real-time 3-point velocity scanner.

    Hooks into the LiveBreakoutEngine's tick flow:
      _process_tick(tick) → velocity_scanner.on_tick(token, ltp, volume, ts, tick)

    All computation is in-memory using deque + numpy. No I/O on the hot path.
    Signal emission is via the on_velocity_signal callback.
    """

    def __init__(
        self,
        watchlist: Dict[int, str],
        trend_map: Dict[int, str],
        on_velocity_signal: Callable[[VelocitySignal], None],
    ):
        self.watchlist = watchlist          # token → symbol
        self.trend_map = trend_map          # token → "ABOVE"/"BELOW"/"N/A"
        self.on_velocity_signal = on_velocity_signal

        # Per-token rolling state
        self.states: Dict[int, TokenVelocityState] = {}
        for token in watchlist:
            self.states[token] = TokenVelocityState()

        # Deduplication: prevent double-fires within the same 1-min bar
        self._fired_signals: Set[tuple] = set()

        # Stats
        self.eligible_count = 0
        self.signals_fired = 0

    # ── 1-Minute Candle Boundary ─────────────────────────────────────────────

    @staticmethod
    def _get_1m_boundary(ts: datetime) -> datetime:
        """Get the start of the current 1-minute candle."""
        return ts.replace(second=0, microsecond=0)

    # ── Core Tick Handler ────────────────────────────────────────────────────

    def on_tick(self, token: int, ltp: float, volume: int, ts: datetime, tick: dict):
        """
        Process a single tick through the velocity scanner pipeline.
        Called from _worker_loop() — must be fast.

        Args:
            token:  Instrument token.
            ltp:    Last traded price.
            volume: Cumulative volume from Kite tick.
            ts:     Exchange timestamp (timezone-aware).
            tick:   Raw tick dict (for WOBI extraction).
        """
        state = self.states.get(token)
        if state is None:
            return

        # ── Step 1: Dynamic Price Band Filter (cached) ───────────────────
        if state._price_eligible is None:
            state._price_eligible = VELOCITY_PRICE_MIN <= ltp <= VELOCITY_PRICE_MAX
            if state._price_eligible:
                self.eligible_count += 1

        if not state._price_eligible:
            # Re-check periodically as price changes (every ~100 ticks via boundary reset)
            return

        state._last_ltp = ltp

        # ── Step 2: 1-Minute Candle Management ──────────────────────────
        boundary = self._get_1m_boundary(ts)
        prev_boundary = state.current_boundary

        if prev_boundary is not None and boundary != prev_boundary:
            # ── CANDLE CLOSED — archive it ──
            if state.current_candle.is_valid():
                state.candle_history.append(MicroCandle(
                    open=state.current_candle.open,
                    high=state.current_candle.high,
                    low=state.current_candle.low,
                    close=state.current_candle.close,
                    volume=state.current_candle.volume,
                    tick_count=state.current_candle.tick_count,
                    boundary=prev_boundary,
                ))

            # Reset current candle
            state.current_candle = MicroCandle(boundary=boundary)

            # Re-check price eligibility on candle close (price may have drifted)
            state._price_eligible = VELOCITY_PRICE_MIN <= ltp <= VELOCITY_PRICE_MAX

        # Update building candle
        state.current_candle.update(ltp, volume)
        state.current_boundary = boundary

        # ── Step 3: Need enough history for ATR + consolidation ─────────
        history_len = len(state.candle_history)
        min_required = max(VELOCITY_ATR_PERIOD, VELOCITY_CONSOLIDATION_BARS, VELOCITY_VOLUME_LOOKBACK)
        if history_len < min_required:
            return

        # ── Step 4: ATR(20) Feasibility Gate ────────────────────────────
        atr = self._compute_atr(state)
        if atr < VELOCITY_ATR_MIN:
            return  # Insufficient velocity — skip

        # ── Step 5: Micro-Consolidation Range (last 3 bars) ─────────────
        consol_high, consol_low = self._get_consolidation_range(state)

        # ── Step 6: Volume Surge Check ──────────────────────────────────
        current_vol = state.current_candle.volume
        avg_vol = self._compute_avg_volume(state)
        if avg_vol <= 0:
            return

        vol_ratio = current_vol / avg_vol

        # ── Step 7: WOBI from latest tick ───────────────────────────────
        wobi = self._compute_wobi(tick)
        if wobi is None:
            return

        # ── Step 8: Velocity Trigger Evaluation ─────────────────────────
        dedup_key = (token, str(boundary))

        # BUY VELOCITY
        if (
            ltp > consol_high
            and vol_ratio >= VELOCITY_VOLUME_MULTIPLIER
            and wobi >= VELOCITY_WOBI_BULL
            and dedup_key not in self._fired_signals
        ):
            bid_qty, ask_qty = self._get_depth_qtys(tick)
            signal = VelocitySignal(
                symbol=self.watchlist[token],
                token=token,
                direction="BUY",
                signal_type="3PT_VELOCITY_BUY",
                trigger_price=round(ltp, 2),
                target_price=round(ltp + VELOCITY_SCALP_TARGET, 2),
                stop_loss=round(ltp - VELOCITY_STOP_LOSS, 2),
                wobi=wobi,
                atr_1m=round(atr, 4),
                volume_ratio=round(vol_ratio, 2),
                consolidation_high=round(consol_high, 2),
                consolidation_low=round(consol_low, 2),
                current_volume=current_vol,
                avg_volume=round(avg_vol, 0),
                total_bid_qty=bid_qty,
                total_ask_qty=ask_qty,
                trend=self.trend_map.get(token, "N/A"),
                timestamp=ts,
            )
            self._fired_signals.add(dedup_key)
            self.signals_fired += 1
            self.on_velocity_signal(signal)

        # SELL VELOCITY
        elif (
            ltp < consol_low
            and vol_ratio >= VELOCITY_VOLUME_MULTIPLIER
            and wobi <= VELOCITY_WOBI_BEAR
            and dedup_key not in self._fired_signals
        ):
            bid_qty, ask_qty = self._get_depth_qtys(tick)
            signal = VelocitySignal(
                symbol=self.watchlist[token],
                token=token,
                direction="SELL",
                signal_type="3PT_VELOCITY_SELL",
                trigger_price=round(ltp, 2),
                target_price=round(ltp - VELOCITY_SCALP_TARGET, 2),
                stop_loss=round(ltp + VELOCITY_STOP_LOSS, 2),
                wobi=wobi,
                atr_1m=round(atr, 4),
                volume_ratio=round(vol_ratio, 2),
                consolidation_high=round(consol_high, 2),
                consolidation_low=round(consol_low, 2),
                current_volume=current_vol,
                avg_volume=round(avg_vol, 0),
                total_bid_qty=bid_qty,
                total_ask_qty=ask_qty,
                trend=self.trend_map.get(token, "N/A"),
                timestamp=ts,
            )
            self._fired_signals.add(dedup_key)
            self.signals_fired += 1
            self.on_velocity_signal(signal)

    # ── ATR Computation (numpy, zero-alloc) ──────────────────────────────────

    def _compute_atr(self, state: TokenVelocityState) -> float:
        """
        Compute 1-minute ATR over the last VELOCITY_ATR_PERIOD completed bars.
        Uses pre-allocated numpy buffer to avoid GC churn.
        """
        history = state.candle_history
        n = min(VELOCITY_ATR_PERIOD, len(history))
        if n == 0:
            return 0.0

        buf = state._atr_buffer
        for i in range(n):
            candle = history[-(n - i)]   # oldest → newest within the window
            tr_hl = candle.high - candle.low
            if i > 0:
                prev_close = history[-(n - i + 1)].close
                tr_hc = abs(candle.high - prev_close)
                tr_lc = abs(candle.low - prev_close)
                buf[i] = max(tr_hl, tr_hc, tr_lc)
            else:
                buf[i] = tr_hl

        return float(np.mean(buf[:n]))

    # ── Consolidation Range ──────────────────────────────────────────────────

    @staticmethod
    def _get_consolidation_range(state: TokenVelocityState) -> tuple:
        """
        Get the max-high and min-low of the last VELOCITY_CONSOLIDATION_BARS
        completed 1-min candles (micro-consolidation envelope).
        """
        n = VELOCITY_CONSOLIDATION_BARS
        history = state.candle_history
        if len(history) < n:
            return 0.0, float("inf")

        max_high = -float("inf")
        min_low = float("inf")
        for i in range(1, n + 1):
            c = history[-i]
            if c.high > max_high:
                max_high = c.high
            if c.low < min_low:
                min_low = c.low

        return max_high, min_low

    # ── Average Volume ───────────────────────────────────────────────────────

    def _compute_avg_volume(self, state: TokenVelocityState) -> float:
        """Average volume of the last VELOCITY_VOLUME_LOOKBACK completed bars."""
        history = state.candle_history
        n = min(VELOCITY_VOLUME_LOOKBACK, len(history))
        if n == 0:
            return 0.0

        buf = state._volume_buffer
        for i in range(n):
            buf[i] = history[-(n - i)].volume

        return float(np.mean(buf[:n]))

    # ── WOBI from Kite Depth (inline, no import overhead) ────────────────────

    @staticmethod
    def _compute_wobi(tick: dict) -> Optional[float]:
        """
        Tier-weighted OBI from Kite's Level 2 depth.
        Identical weighting to live_engine.calculate_obi() but inlined
        to avoid function-call overhead on the hot path.
        """
        from config import OBI_TIER_WEIGHTS

        depth = tick.get("depth")
        if not depth:
            return None

        buy_levels = depth.get("buy", [])
        sell_levels = depth.get("sell", [])
        if not buy_levels or not sell_levels:
            return None

        weights = OBI_TIER_WEIGHTS
        w_bid = 0.0
        w_ask = 0.0

        for i in range(min(5, len(buy_levels), len(sell_levels))):
            w = weights[i] if i < len(weights) else 0.1
            w_bid += buy_levels[i].get("quantity", 0) * w
            w_ask += sell_levels[i].get("quantity", 0) * w

        total = w_bid + w_ask
        if total == 0:
            return 0.0

        return round((w_bid - w_ask) / total, 4)

    # ── Depth Quantities ─────────────────────────────────────────────────────

    @staticmethod
    def _get_depth_qtys(tick: dict) -> tuple:
        """Extract total raw bid/ask quantities from tick depth."""
        depth = tick.get("depth", {})
        bid = sum(l.get("quantity", 0) for l in depth.get("buy", []))
        ask = sum(l.get("quantity", 0) for l in depth.get("sell", []))
        return bid, ask

    # ── Dedup Cleanup (call periodically to prevent memory leak) ─────────

    def cleanup_dedup(self):
        """Purge old dedup keys. Call every ~5 minutes from the main loop."""
        if len(self._fired_signals) > 5000:
            self._fired_signals.clear()

    # ── Status ───────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return scanner status for periodic logging."""
        active = sum(
            1 for s in self.states.values()
            if s._price_eligible and len(s.candle_history) >= VELOCITY_ATR_PERIOD
        )
        return {
            "eligible": self.eligible_count,
            "active_scanning": active,
            "signals_fired": self.signals_fired,
            "dedup_cache_size": len(self._fired_signals),
        }
