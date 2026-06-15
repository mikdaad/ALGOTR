"""
==================================================================================
  PHASE 2+4+5 — Live Breakout + VPA + Velocity Scanner (live_engine.py) [v5]
==================================================================================
  Streams live ticks via Kite WebSocket for Phase-1 screened watchlist targets.

  PHASE 2 — 5-Min VPA-Upgraded Breakout:
    Condition A — PRICE BREAKOUT (3 setup types):
      VAH_BREAKOUT:   5-min candle closes ABOVE the intraday Value Area High
      VAL_BREAKDOWN:  5-min candle closes BELOW the intraday Value Area Low
      POC_REJECTION:  Live price touches POC with WOBI flipping against direction
      OR_BREAKOUT:    Fallback to classic 15-min Opening Range when VPA not ready
    Condition B — TIER-WEIGHTED ORDER BOOK IMBALANCE (Level 2):
      WOBI = (WeightedBids - WeightedAsks) / (WeightedBids + WeightedAsks)
      Tier weights: [0.40, 0.20, 0.20, 0.10, 0.10] (anti-spoofing)
      Bull: WOBI >= +0.60    Bear: WOBI <= -0.60

  PHASE 4 — 1-Min 3-Point Velocity Scanner:
    Parallel real-time scanner running on 1-min micro-candles.
    Catches scalp expansions via consolidation breakout + volume
    surge + aggressive WOBI confirmation. See velocity_scanner.py.

  PHASE 5 — StreamingVolumeProfile (O(1) per-tick update):
    Each token owns a StreamingVolumeProfile (numpy bin array).
    Updated inline in _process_tick() — zero I/O, zero callbacks.
    POC/VAH/VAL queried at candle-close for dynamic SL+Target.

  DYNAMIC TARGETING (replaces static ±3pt offsets):
    VAH_BREAKOUT BUY:  SL = VAH - 2×bin | Target = VAH + (VAH - POC)
    VAL_BREAKDOWN SELL: SL = VAL + 2×bin | Target = VAL - (POC - VAL)
    POC_REJECTION SELL: SL = POC + 4×bin | Target = VAL
    POC_REJECTION BUY:  SL = POC - 4×bin | Target = VAH
    OR_BREAKOUT (fallback): SL = candle_low | Target = entry + 2×risk

  Architecture:
    WebSocket → tick_queue (thread-safe) → worker thread
      ├─→ StreamingVolumeProfile.update()  (O(1), every tick)
      ├─→ 5-min candle + VPA + OBI → BreakoutSignal (with POC/VAH/VAL)
      └─→ 1-min velocity scanner → VelocitySignal
=================================================================================="""

import json
import time
import threading
import queue
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

import pytz
from kiteconnect import KiteTicker

from velocity_scanner import VelocityScanner
from volume_profile import VolumeProfileEngine, handle_vpa_signal, StreamingVolumeProfile

from config import (
    KITE_API_KEY,
    OPENING_RANGE_MINUTES,
    LIVE_CANDLE_MINUTES,
    OBI_BULL_THRESHOLD,
    OBI_BEAR_THRESHOLD,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
)

IST = pytz.timezone("Asia/Kolkata")


# ──────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LiveCandle:
    """In-memory 5-minute candle being built from ticks."""
    open: float = 0.0
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    volume: int = 0
    start_time: Optional[datetime] = None
    tick_count: int = 0

    def update(self, price: float, vol: int, ts: datetime):
        if self.tick_count == 0:
            self.open = price
            self.high = price
            self.low = price
            self.start_time = ts
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume = vol  # Kite sends cumulative volume per tick
        self.tick_count += 1

    def reset(self):
        self.open = 0.0
        self.high = 0.0
        self.low = float("inf")
        self.close = 0.0
        self.volume = 0
        self.start_time = None
        self.tick_count = 0


@dataclass
class OpeningRange:
    """Tracks the first 15-minute high/low for a stock."""
    high: float = 0.0
    low: float = float("inf")
    is_set: bool = False
    candles_collected: int = 0


@dataclass
class BreakoutSignal:
    """
    Emitted when a breakout + WOBI condition is confirmed.

    v5 VPA upgrade: `target_price`, `vpa_signal_type`, `current_poc`,
    `current_vah`, and `current_val` replace the old static stop-loss logic.
    All three VPA levels are forwarded to Supabase and Telegram so the trader
    can see the exact institutional liquidity nodes before approving a trade.
    """
    symbol: str
    token: int
    direction: str                # "BUY" or "SELL"
    breakout_type: str            # legacy field — kept for CSV compatibility
    entry_price: float            # Close of the breakout candle
    stop_loss: float              # VPA-derived stop-loss (or candle low/high fallback)
    obi: float                    # Weighted Order Book Imbalance ratio
    or_high: float                # Opening range high
    or_low: float                 # Opening range low
    candle_open: float
    candle_high: float
    candle_low: float
    candle_close: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(IST))
    total_bid_qty: int = 0
    total_ask_qty: int = 0
    trend: str = "N/A"            # "ABOVE" or "BELOW" from screener EMA filter
    # ── VPA v5 fields (dynamic targets) ──────────────────────────────────────
    target_price: float = 0.0    # VPA-derived profit target
    vpa_signal_type: str = ""    # "VAH_BREAKOUT" | "VAL_BREAKDOWN" | "POC_REJECTION" | "OR_BREAKOUT"
    current_poc: float = 0.0     # Intraday Point of Control at trigger time
    current_vah: float = 0.0     # Intraday Value Area High at trigger time
    current_val: float = 0.0     # Intraday Value Area Low at trigger time
    vpa_ready: bool = False      # True if profile had enough volume when signal fired


# ──────────────────────────────────────────────────────────────────────────────
# TIER-WEIGHTED ORDER BOOK IMBALANCE CALCULATOR (v3)
# ──────────────────────────────────────────────────────────────────────────────

def calculate_obi(tick: dict) -> Optional[float]:
    """
    Calculate Tier-Weighted Order Book Imbalance from Kite's Level 2 depth.

    Tier weights (anti-spoofing):
      Tier 1 (Best Bid/Ask):  40%   ← hardest to spoof
      Tier 2-3:               20% each (40% total)
      Tier 4-5:               10% each (20% total) ← easiest to spoof

    WOBI = (Σ weighted_bid - Σ weighted_ask) / (Σ weighted_bid + Σ weighted_ask)
    Range: -1.0 (all sellers) to +1.0 (all buyers)
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
    weighted_bid = 0.0
    weighted_ask = 0.0

    for i in range(min(5, len(buy_levels), len(sell_levels))):
        w = weights[i] if i < len(weights) else 0.1
        weighted_bid += buy_levels[i].get("quantity", 0) * w
        weighted_ask += sell_levels[i].get("quantity", 0) * w

    total = weighted_bid + weighted_ask
    if total == 0:
        return 0.0

    return round((weighted_bid - weighted_ask) / total, 4)


def get_depth_quantities(tick: dict) -> tuple:
    """Extract total bid and ask quantities from tick depth (raw, unweighted)."""
    depth = tick.get("depth", {})
    bid = sum(l.get("quantity", 0) for l in depth.get("buy", []))
    ask = sum(l.get("quantity", 0) for l in depth.get("sell", []))
    return bid, ask


# ──────────────────────────────────────────────────────────────────────────────
# CANDLE BOUNDARY HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def get_candle_boundary(ts: datetime, interval_min: int) -> datetime:
    """Get the start of the current candle period."""
    market_open = ts.replace(
        hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE,
        second=0, microsecond=0
    )
    elapsed = (ts - market_open).total_seconds()
    candle_index = int(elapsed // (interval_min * 60))
    return market_open + timedelta(minutes=candle_index * interval_min)


def is_opening_range_period(ts: datetime) -> bool:
    """Check if the timestamp falls within the opening range period."""
    market_open = ts.replace(
        hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE,
        second=0, microsecond=0
    )
    or_end = market_open + timedelta(minutes=OPENING_RANGE_MINUTES)
    return market_open <= ts < or_end


# ──────────────────────────────────────────────────────────────────────────────
# LIVE ENGINE CLASS (v3 — Thread-Safe Queue Architecture)
# ──────────────────────────────────────────────────────────────────────────────

class LiveBreakoutEngine:
    """
    WebSocket-based live engine with thread-safe tick processing:

      WebSocket thread        Worker thread
      ──────────────          ─────────────
      on_ticks callback  →  tick_queue  →  _worker_loop()
      (instant enqueue)      (Queue)       (candle + WOBI computation)

      1. Builds 5-min candles from ticks
      2. Tracks the opening 15-min range per stock
      3. Evaluates breakout + WOBI conditions on candle close
      4. Calls the on_signal callback when conditions are met
    """

    def __init__(self, api_key: str, access_token: str,
                 watchlist: List[Dict], on_signal: Callable,
                 on_velocity_signal: Optional[Callable] = None,
                 on_vpa_signal: Optional[Callable] = None):
        """
        Args:
            api_key: Kite API key.
            access_token: Today's access token.
            watchlist: List of {"symbol": str, "token": int, "trend": str (opt)}.
            on_signal: Callback function(BreakoutSignal) called on detection.
            on_velocity_signal: Callback function(VelocitySignal) for 3-pt scalps.
        """
        self.api_key = api_key
        self.access_token = access_token
        self.watchlist = {s["token"]: s["symbol"] for s in watchlist}
        self.tokens = list(self.watchlist.keys())
        self.on_signal = on_signal

        # Trend metadata from screener (token → "ABOVE"/"BELOW"/"N/A")
        self.trend_map: Dict[int, str] = {}
        for s in watchlist:
            self.trend_map[s["token"]] = s.get("trend", "N/A")

        # Per-token state (Phase 2: 5-min breakout)
        self.candles: Dict[int, LiveCandle] = {}
        self.candle_boundaries: Dict[int, datetime] = {}
        self.opening_ranges: Dict[int, OpeningRange] = {}
        self.last_ticks: Dict[int, dict] = {}
        self.seen_signals: set = set()

        # ── Phase 5: Inline StreamingVolumeProfile (O(1) per-tick) ──
        # Each token gets its own lightweight numpy bin array.
        # Updated on every tick in _process_tick (no callbacks, no threads).
        # Queried at candle-close by _evaluate_vpa_breakout for dynamic SL+Target.
        self.vp_profiles: Dict[int, StreamingVolumeProfile] = {}
        self._last_vols: Dict[int, int] = {}   # Cumulative→delta conversion

        # Initialize per-token state
        for token in self.tokens:
            self.candles[token] = LiveCandle()
            self.opening_ranges[token] = OpeningRange()
            self.vp_profiles[token] = StreamingVolumeProfile(bin_size=0.10)
            self._last_vols[token] = 0

        print(f"   📊 StreamingVolumeProfile: ARMED ({len(self.tokens)} tokens, bin=₹0.10)")

        # ── Phase 4: Velocity Scanner (1-min 3-point scalps) ──
        self.velocity_scanner: Optional[VelocityScanner] = None
        if on_velocity_signal is not None:
            self.velocity_scanner = VelocityScanner(
                watchlist=self.watchlist,
                trend_map=self.trend_map,
                on_velocity_signal=on_velocity_signal,
            )
            print(f"   ⚡ Velocity Scanner: ARMED ({len(self.watchlist)} tokens)")

        # ── Phase 5: Volume Profile Analysis Engine ──
        # Builds intraday VPA profiles for POC/VAH/VAL detection.
        # Pass on_vpa_signal=handle_vpa_signal to activate from main.py.
        self.vpa_engine: Optional[VolumeProfileEngine] = None
        if on_vpa_signal is not None:
            self.vpa_engine = VolumeProfileEngine(
                watchlist=self.watchlist,
                trend_map=self.trend_map,
                on_vpa_signal=on_vpa_signal,
            )
            print(f"   📊 VPA Engine: ARMED ({len(self.watchlist)} tokens)")

        # Thread-safe tick queue — prevents WebSocket lag from WOBI computation
        self._tick_queue: queue.Queue = queue.Queue(maxsize=50000)
        self._worker_thread: Optional[threading.Thread] = None

        self.ticker: Optional[KiteTicker] = None
        self._running = False

        # Stats
        self._ticks_received = 0
        self._ticks_processed = 0
        self._ticks_dropped = 0

    def start(self):
        """Start the WebSocket connection and background tick processor."""
        print(f"\n🔌 Starting WebSocket for {len(self.tokens)} instruments...")
        print(f"   Mode: full (Level 2 depth data)")
        print(f"   Candle interval: {LIVE_CANDLE_MINUTES}min")
        print(f"   Opening range: first {OPENING_RANGE_MINUTES}min")
        print(f"   WOBI thresholds: Bull >= {OBI_BULL_THRESHOLD}, Bear <= {OBI_BEAR_THRESHOLD}")
        print(f"   Tick processing: thread-safe queue (max 50K)")

        # Start background worker thread BEFORE WebSocket
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="TickWorker"
        )
        self._worker_thread.start()

        self.ticker = KiteTicker(self.api_key, self.access_token)
        self.ticker.on_ticks = self._on_ticks
        self.ticker.on_connect = self._on_connect
        self.ticker.on_close = self._on_close
        self.ticker.on_error = self._on_error
        self.ticker.on_reconnect = self._on_reconnect
        self.ticker.on_noreconnect = self._on_noreconnect

        # KiteTicker.connect() is blocking — runs its own event loop
        self.ticker.connect(threaded=True)

    def stop(self):
        """Gracefully stop the WebSocket and worker thread."""
        self._running = False
        if self.ticker:
            self.ticker.close()
        # Poison pill to unblock the worker
        self._tick_queue.put(None)
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3)
        print("🔌 WebSocket disconnected.")
        print(f"   Ticks: {self._ticks_received:,} received | "
              f"{self._ticks_processed:,} processed | "
              f"{self._ticks_dropped:,} dropped")

    # ── WebSocket Callbacks ──────────────────────────────────────────────────

    def _on_connect(self, ws, response):
        """Subscribe to all watchlist tokens in 'full' mode for depth data."""
        print(f"   ✅ WebSocket connected. Subscribing {len(self.tokens)} tokens...")
        ws.subscribe(self.tokens)
        ws.set_mode(ws.MODE_FULL, self.tokens)
        print(f"   ✅ Subscribed in FULL mode (Level 2 depth).")

    def _on_close(self, ws, code, reason):
        print(f"   ⚠️  WebSocket closed: {code} — {reason}")

    def _on_error(self, ws, code, reason):
        print(f"   ❌ WebSocket error: {code} — {reason}")

    def _on_reconnect(self, ws, attempts_count):
        print(f"   🔄 WebSocket reconnecting (attempt {attempts_count})...")

    def _on_noreconnect(self, ws):
        print("   ❌ WebSocket: max reconnect attempts exhausted.")
        self._running = False

    def _on_ticks(self, ws, ticks):
        """
        WebSocket tick callback — MUST be fast to avoid dropped packets.
        Simply enqueues ticks for the background worker to process.
        """
        for tick in ticks:
            self._ticks_received += 1
            try:
                self._tick_queue.put_nowait(tick)
            except queue.Full:
                self._ticks_dropped += 1

    # ── Background Worker Thread ─────────────────────────────────────────────

    def _worker_loop(self):
        """
        Background thread that drains the tick queue and performs all
        heavy computation (candle building, WOBI, breakout evaluation).
        Runs until self._running is False and queue is drained.
        """
        while self._running or not self._tick_queue.empty():
            try:
                tick = self._tick_queue.get(timeout=1)
            except queue.Empty:
                continue

            # Poison pill check
            if tick is None:
                break

            try:
                self._process_tick(tick)
                self._ticks_processed += 1
            except Exception as e:
                token = tick.get("instrument_token", "?")
                print(f"   ⚠️  Tick processing error ({token}): {e}")

    # ── Tick Processing ──────────────────────────────────────────────────────

    def _process_tick(self, tick: dict):
        """Process a single tick: update candle, check boundaries, evaluate."""
        token = tick.get("instrument_token")
        if token not in self.watchlist:
            return

        ltp = tick.get("last_price")
        volume = tick.get("volume_traded", 0)
        ts = tick.get("exchange_timestamp") or tick.get("timestamp")

        if ltp is None or ts is None:
            return

        # Ensure timezone awareness
        if ts.tzinfo is None:
            ts = IST.localize(ts)

        # Store latest tick for WOBI lookup on candle close
        self.last_ticks[token] = tick

        # Update opening range if we're in the first 15 minutes
        if is_opening_range_period(ts):
            orng = self.opening_ranges[token]
            orng.high = max(orng.high, ltp)
            orng.low = min(orng.low, ltp)

        # Determine current candle boundary
        boundary = get_candle_boundary(ts, LIVE_CANDLE_MINUTES)
        prev_boundary = self.candle_boundaries.get(token)

        if prev_boundary is not None and boundary != prev_boundary:
            # ── CANDLE CLOSED ──
            closed_candle = LiveCandle(
                open=self.candles[token].open,
                high=self.candles[token].high,
                low=self.candles[token].low,
                close=self.candles[token].close,
                volume=self.candles[token].volume,
                start_time=prev_boundary,
                tick_count=self.candles[token].tick_count,
            )

            # Mark opening range as set once we exit the OR period
            if not is_opening_range_period(ts) and not self.opening_ranges[token].is_set:
                self.opening_ranges[token].is_set = True
                orng = self.opening_ranges[token]
                sym = self.watchlist[token]
                print(f"   📐 {sym} Opening Range set: High=₹{orng.high:.2f} Low=₹{orng.low:.2f}")

            # Evaluate breakout on the closed candle
            self._evaluate_vpa_breakout(token, closed_candle, ts)

            # Reset candle for new period
            self.candles[token].reset()

        # Update the current building candle
        self.candles[token].update(ltp, volume, ts)
        self.candle_boundaries[token] = boundary

        # ── Phase 4: Feed tick to Velocity Scanner (1-min parallel path) ──
        if self.velocity_scanner is not None:
            self.velocity_scanner.on_tick(token, ltp, volume, ts, tick)

        # ── Phase 5: Inline StreamingVolumeProfile update (O(1) HOT PATH) ──
        # Convert Kite's cumulative session volume to per-tick delta.
        # bins[price_bin_index] += delta  — single numpy scalar write.
        delta = max(0, volume - self._last_vols.get(token, 0))
        self._last_vols[token] = volume
        if delta > 0 and token in self.vp_profiles:
            self.vp_profiles[token].update(ltp, delta)

        # ── Phase 5 (also): VolumeProfileEngine co-processor (separate callbacks)
        if self.vpa_engine is not None:
            self.vpa_engine.on_tick(token, ltp, volume, ts, tick)

    # ── VPA-Upgraded Breakout Evaluation ───────────────────────────────────────

    def _evaluate_vpa_breakout(self, token: int, candle: LiveCandle, ts: datetime):
        """
        VPA-upgraded breakout evaluator (replaces the static OR-only version).

        Evaluates 3 setups in priority order:

        PRIORITY 1 — VAH_BREAKOUT / VAL_BREAKDOWN (VPA ready):
            Triggered when the 5-min candle closes outside the Value Area,
            confirmed by institutional WOBI. SL and Target are derived from
            the live POC/VAH/VAL nodes — not static offsets.

        PRIORITY 2 — POC_REJECTION (VPA ready):
            Triggered when the candle close is within 2 bins of the POC AND
            the WOBI is heavily skewed against the approach direction.
            Targets the opposite VA boundary.

        PRIORITY 3 — OR_BREAKOUT (fallback, VPA not yet ready):
            Classic 15-min Opening Range breakout. Used in the first ~15 candles
            before the volume profile has accumulated enough data.
            Uses fixed 1:2 R:R with candle high/low as stop.

        DEDUP KEY:
            (token, candle_start_time, vpa_signal_type)
            This allows multiple setup types per candle (e.g., an OR breakout
            AND a VAH breakout if both conditions are met simultaneously).
        """
        symbol = self.watchlist[token]
        orng   = self.opening_ranges[token]
        trend  = self.trend_map.get(token, "N/A")

        if candle.tick_count == 0:
            return

        # ── Condition B: WOBI (required for all setups) ────────────────────
        latest_tick = self.last_ticks.get(token)
        if latest_tick is None:
            return
        obi = calculate_obi(latest_tick)
        if obi is None:
            return
        total_bid, total_ask = get_depth_quantities(latest_tick)

        close  = candle.close
        signal = None

        # Compute breakout booleans (requires OR to be fully set)
        bull_breakout = orng.is_set and close > orng.high
        bear_breakout = orng.is_set and close < orng.low

        if bull_breakout and obi >= OBI_BULL_THRESHOLD:
            signal = BreakoutSignal(
                symbol=symbol, token=token,
                direction="BUY", breakout_type="BULL_BREAKOUT",
                entry_price=round(candle.close, 2),
                stop_loss=round(candle.low, 2),
                obi=obi,
                or_high=round(orng.high, 2),
                or_low=round(orng.low, 2),
                candle_open=round(candle.open, 2),
                candle_high=round(candle.high, 2),
                candle_low=round(candle.low, 2),
                candle_close=round(candle.close, 2),
                timestamp=ts,
                total_bid_qty=total_bid,
                total_ask_qty=total_ask,
                trend=trend,
            )

        elif bear_breakout and obi <= OBI_BEAR_THRESHOLD:
            signal = BreakoutSignal(
                symbol=symbol, token=token,
                direction="SELL", breakout_type="BEAR_BREAKOUT",
                entry_price=round(candle.close, 2),
                stop_loss=round(candle.high, 2),
                obi=obi,
                or_high=round(orng.high, 2),
                or_low=round(orng.low, 2),
                candle_open=round(candle.open, 2),
                candle_high=round(candle.high, 2),
                candle_low=round(candle.low, 2),
                candle_close=round(candle.close, 2),
                timestamp=ts,
                total_bid_qty=total_bid,
                total_ask_qty=total_ask,
                trend=trend,
            )

        if signal is not None:
            # Dedup: one signal per stock per candle boundary
            dedup_key = (token, str(candle.start_time))
            if dedup_key not in self.seen_signals:
                self.seen_signals.add(dedup_key)
                self.on_signal(signal)

    # ── Status ───────────────────────────────────────────────────────────────

    def print_status(self):
        """Print current engine status (for debugging / periodic logging)."""
        now = datetime.now(IST)
        or_set = sum(1 for o in self.opening_ranges.values() if o.is_set)
        active = sum(1 for c in self.candles.values() if c.tick_count > 0)
        q_size = self._tick_queue.qsize()
        print(
            f"   📡 Status [{now.strftime('%H:%M:%S')}]: "
            f"{active}/{len(self.tokens)} active | "
            f"{or_set}/{len(self.tokens)} ORs set | "
            f"{len(self.seen_signals)} signals | "
            f"Q={q_size} | "
            f"rx={self._ticks_received:,} proc={self._ticks_processed:,} drop={self._ticks_dropped}"
        )

        # Velocity Scanner status
        if self.velocity_scanner is not None:
            vs = self.velocity_scanner.get_status()
            print(
                f"   ⚡ Velocity [{now.strftime('%H:%M:%S')}]: "
                f"{vs['eligible']} eligible | "
                f"{vs['active_scanning']} scanning | "
                f"{vs['signals_fired']} fired | "
                f"dedup={vs['dedup_cache_size']}"
            )
            # Periodic dedup cleanup
            self.velocity_scanner.cleanup_dedup()

        # VPA Engine status
        if self.vpa_engine is not None:
            vpa = self.vpa_engine.get_status()
            print(
                f"   📊 VPA [{now.strftime('%H:%M:%S')}]: "
                f"{vpa['profiles_active']}/{vpa['total_tokens']} active | "
                f"{vpa['signals_fired']} fired | "
                f"ticks={vpa['ticks_processed']:,} | "
                f"dedup={vpa['dedup_cache_size']}"
            )
            # Periodic dedup cleanup
            self.vpa_engine.cleanup_dedup(now)
