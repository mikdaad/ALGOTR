"""
==================================================================================
  PHASE 2 — Live Breakout + Tier-Weighted OBI Engine (live_engine.py) [v3]
==================================================================================
  Streams live ticks via Kite WebSocket for Phase-1 screened watchlist targets.

  Detects "Strong Breakout Alerts" when BOTH conditions are simultaneous:

    Condition A — PRICE BREAKOUT:
      5-min candle closes above the Opening 15-Min High (Bull)
      or below the Opening 15-Min Low (Bear).

    Condition B — TIER-WEIGHTED ORDER BOOK IMBALANCE (Level 2):
      WOBI = (WeightedBids - WeightedAsks) / (WeightedBids + WeightedAsks)
      Tier weights: [0.40, 0.20, 0.20, 0.10, 0.10] (anti-spoofing)
      Bull: WOBI ≥ +0.60    Bear: WOBI ≤ -0.60

  v3 UPGRADES:
    - Thread-safe queue: WebSocket callback enqueues ticks instantly, a
      background worker drains the queue for candle/OBI computation.
      This prevents WOBI calculation from causing dropped packets.
    - Trend metadata: accepts optional "trend" field from screener
      watchlist to filter directional signals (ABOVE-EMA only for buys).
    - Tier-weighted OBI anti-spoofing (40/40/20 tiering).

  Architecture:
    WebSocket → tick_queue (thread-safe) → worker thread → candle + OBI
    → BreakoutSignal → on_signal callback → alerts.py
==================================================================================
"""

import json
import time
import threading
import queue
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

import pytz
from kiteconnect import KiteTicker

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
    """Emitted when a breakout + WOBI condition is confirmed."""
    symbol: str
    token: int
    direction: str                # "BUY" or "SELL"
    breakout_type: str            # "BULL_BREAKOUT" or "BEAR_BREAKOUT"
    entry_price: float            # Close of the breakout candle
    stop_loss: float              # Low (bull) or High (bear) of breakout candle
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
                 watchlist: List[Dict], on_signal: Callable):
        """
        Args:
            api_key: Kite API key.
            access_token: Today's access token.
            watchlist: List of {"symbol": str, "token": int, "trend": str (opt)}.
            on_signal: Callback function(BreakoutSignal) called on detection.
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

        # Per-token state
        self.candles: Dict[int, LiveCandle] = {}
        self.candle_boundaries: Dict[int, datetime] = {}
        self.opening_ranges: Dict[int, OpeningRange] = {}
        self.last_ticks: Dict[int, dict] = {}
        self.seen_signals: set = set()

        # Initialize state for each token
        for token in self.tokens:
            self.candles[token] = LiveCandle()
            self.opening_ranges[token] = OpeningRange()

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
            self._evaluate_breakout(token, closed_candle, ts)

            # Reset candle for new period
            self.candles[token].reset()

        # Update the current building candle
        self.candles[token].update(ltp, volume, ts)
        self.candle_boundaries[token] = boundary

    # ── Breakout Evaluation ──────────────────────────────────────────────────

    def _evaluate_breakout(self, token: int, candle: LiveCandle, ts: datetime):
        """
        Check Condition A (price breakout) and Condition B (WOBI) simultaneously.
        Also applies trend filter: BUY only for ABOVE-EMA stocks.
        """
        symbol = self.watchlist[token]
        orng = self.opening_ranges[token]
        trend = self.trend_map.get(token, "N/A")

        # Opening range must be established first
        if not orng.is_set:
            return

        # Skip if candle has no data
        if candle.tick_count == 0:
            return

        # ── Condition A: Price Breakout ──
        bull_breakout = candle.close > orng.high
        bear_breakout = candle.close < orng.low

        if not bull_breakout and not bear_breakout:
            return

        # ── Trend Filter ──
        # BUY signals only for stocks above their 20-EMA (ABOVE trend)
        # SELL signals only for stocks below their 20-EMA (BELOW trend)
        if bull_breakout and trend == "BELOW":
            return  # Skip: buying into a downtrend
        if bear_breakout and trend == "ABOVE":
            return  # Skip: shorting into an uptrend

        # ── Condition B: Tier-Weighted Order Book Imbalance ──
        latest_tick = self.last_ticks.get(token)
        if latest_tick is None:
            return

        obi = calculate_obi(latest_tick)
        if obi is None:
            return

        total_bid, total_ask = get_depth_quantities(latest_tick)

        # Check WOBI thresholds
        signal = None

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
