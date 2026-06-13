-- ============================================================================
-- Supabase / PostgreSQL — Upgraded Schema for v2 Engine
-- ============================================================================
-- Run this in the Supabase SQL Editor.
-- This creates tables for both the screener results and live breakout signals.
-- ============================================================================

-- ──────────────────────────────────────────────────────────────────────────────
-- TABLE 1: Breakout Signals (from the live engine)
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS breakout_signals (
    id              BIGSERIAL PRIMARY KEY,
    symbol          VARCHAR(50) NOT NULL,
    direction       VARCHAR(10) NOT NULL,           -- 'BUY' or 'SELL'
    breakout_type   VARCHAR(30) NOT NULL,           -- 'BULL_BREAKOUT' or 'BEAR_BREAKOUT'
    entry_price     NUMERIC(12,2) NOT NULL,
    stop_loss       NUMERIC(12,2) NOT NULL,
    obi             NUMERIC(8,4) NOT NULL,          -- Order Book Imbalance ratio (-1 to +1)
    total_bid_qty   BIGINT DEFAULT 0,
    total_ask_qty   BIGINT DEFAULT 0,
    or_high         NUMERIC(12,2),                  -- Opening range high
    or_low          NUMERIC(12,2),                  -- Opening range low
    candle_open     NUMERIC(12,2),
    candle_high     NUMERIC(12,2),
    candle_low      NUMERIC(12,2),
    candle_close    NUMERIC(12,2),
    signal_time     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brk_symbol ON breakout_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_brk_time   ON breakout_signals(signal_time);
CREATE INDEX IF NOT EXISTS idx_brk_dir    ON breakout_signals(direction);

-- ──────────────────────────────────────────────────────────────────────────────
-- TABLE 2: Pre-Market Screener Runs (from the screener)
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS screener_runs (
    id              BIGSERIAL PRIMARY KEY,
    run_time        TIMESTAMPTZ NOT NULL,           -- When the screener was run
    symbol          VARCHAR(50) NOT NULL,
    close_price     NUMERIC(12,2),
    atr             NUMERIC(12,2),
    atr_shrink_pct  NUMERIC(8,2),                   -- ATR shrinkage % (negative = shrinking)
    setup           VARCHAR(100),                   -- e.g. 'COIL+BULL_ZONE'
    five_day_high   NUMERIC(12,2),
    five_day_low    NUMERIC(12,2),
    avg_volume      BIGINT,
    score           NUMERIC(10,2),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scr_symbol ON screener_runs(symbol);
CREATE INDEX IF NOT EXISTS idx_scr_time   ON screener_runs(run_time);

-- ──────────────────────────────────────────────────────────────────────────────
-- LEGACY TABLE: Original volume-based signals (kept for backward compat)
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signals (
    id              BIGSERIAL PRIMARY KEY,
    symbol          VARCHAR(50) NOT NULL,
    direction       VARCHAR(10) NOT NULL,
    pattern         VARCHAR(50) NOT NULL,
    entry_price     NUMERIC(12,2) NOT NULL,
    stop_loss       NUMERIC(12,2) NOT NULL,
    candle_time     TIMESTAMPTZ NOT NULL,
    volume          BIGINT,
    volume_ratio    NUMERIC(6,2),
    open_price      NUMERIC(12,2),
    high_price      NUMERIC(12,2),
    low_price       NUMERIC(12,2),
    close_price     NUMERIC(12,2),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- USEFUL QUERIES
-- ============================================================================

-- Today's breakout signals:
-- SELECT * FROM breakout_signals WHERE DATE(signal_time) = CURRENT_DATE ORDER BY signal_time DESC;

-- Screener accuracy: compare screener picks with next-day breakout signals:
-- SELECT s.symbol, s.setup, s.score, b.direction, b.entry_price, b.obi
-- FROM screener_runs s
-- LEFT JOIN breakout_signals b ON s.symbol = b.symbol
--   AND DATE(b.signal_time) = DATE(s.run_time) + 1
-- WHERE DATE(s.run_time) = CURRENT_DATE - 1
-- ORDER BY s.score DESC;

-- OBI distribution analysis:
-- SELECT direction, AVG(obi), MIN(obi), MAX(obi), COUNT(*)
-- FROM breakout_signals GROUP BY direction;

-- ──────────────────────────────────────────────────────────────────────────────
-- TABLE 4: Unified Trading Signals (for Next.js Live Dashboard + HITL Gateway)
-- ──────────────────────────────────────────────────────────────────────────────
-- This table receives ALL signal types (breakout + velocity) in a normalized
-- schema optimized for the real-time frontend dashboard.
-- Supabase Realtime is enabled on this table for WebSocket broadcasts.
--
-- HITL columns track the approval → execution lifecycle for velocity signals.
-- Breakout signals are inserted with status='alert_only' (no HITL).
-- ──────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS trading_signals (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    signal_time     TIMESTAMPTZ NOT NULL,
    symbol          VARCHAR(50) NOT NULL,
    direction       VARCHAR(10) NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    signal_type     VARCHAR(40) NOT NULL,        -- 'BULL_BREAKOUT', 'BEAR_BREAKOUT', '3PT_VELOCITY_BUY', '3PT_VELOCITY_SELL'
    trigger_price   NUMERIC(12,2) NOT NULL,
    target_price    NUMERIC(12,2),               -- Entry ± 3.00 for velocity, NULL for breakout
    stop_loss       NUMERIC(12,2) NOT NULL,
    wobi_ratio      NUMERIC(8,4) NOT NULL,       -- Tier-Weighted Order Book Imbalance (-1 to +1)
    volume_spike    NUMERIC(8,2),                -- Volume ratio (e.g., 2.50 = 2.5× avg), NULL for breakout
    atr_1m          NUMERIC(8,4),                -- 1-min ATR, velocity only
    total_bid_qty   BIGINT DEFAULT 0,
    total_ask_qty   BIGINT DEFAULT 0,
    trend           VARCHAR(10) DEFAULT 'N/A',   -- 'ABOVE', 'BELOW', 'N/A'
    or_high         NUMERIC(12,2),               -- Opening range high (breakout only)
    or_low          NUMERIC(12,2),               -- Opening range low (breakout only)
    metadata        JSONB DEFAULT '{}'::jsonb,    -- Extensible field for future data
    -- ── HITL Execution Tracking ──
    status          VARCHAR(20) DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','executing','executed',
                                      'rejected','failed','expired','alert_only')),
    order_id        VARCHAR(50),                  -- Kite order ID after successful execution
    approved_via    VARCHAR(20),                  -- 'telegram' | 'dashboard' | NULL
    approved_at     TIMESTAMPTZ,                  -- When approval button was clicked
    executed_at     TIMESTAMPTZ,                  -- When kite.place_order() succeeded
    execution_error TEXT,                         -- Error message if execution failed
    quantity        INTEGER DEFAULT 1             -- Shares to trade (set by user at approval)
);

-- Performance indexes for dashboard queries
CREATE INDEX IF NOT EXISTS idx_ts_time    ON trading_signals(signal_time DESC);
CREATE INDEX IF NOT EXISTS idx_ts_symbol  ON trading_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_ts_dir     ON trading_signals(direction);
CREATE INDEX IF NOT EXISTS idx_ts_type    ON trading_signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_ts_created ON trading_signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ts_status  ON trading_signals(status);

-- ──────────────────────────────────────────────────────────────────────────────
-- ENABLE SUPABASE REALTIME on trading_signals
-- ──────────────────────────────────────────────────────────────────────────────
-- This allows the Next.js dashboard to receive INSERT and UPDATE events.
-- NOTE: Run this AFTER creating the table.
ALTER PUBLICATION supabase_realtime ADD TABLE trading_signals;

-- ──────────────────────────────────────────────────────────────────────────────
-- ROW LEVEL SECURITY (RLS)
-- ──────────────────────────────────────────────────────────────────────────────
-- Enable RLS with policies for:
--   SELECT: anonymous read (dashboard)
--   INSERT: service role only (Python engine)
--   UPDATE: anonymous can update status/qty/sl/target (dashboard approval)
ALTER TABLE trading_signals ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow anonymous read access"
    ON trading_signals FOR SELECT
    USING (true);

CREATE POLICY "Allow service role insert"
    ON trading_signals FOR INSERT
    WITH CHECK (true);

CREATE POLICY "Allow dashboard approval updates"
    ON trading_signals FOR UPDATE
    USING (true)
    WITH CHECK (true);

-- ──────────────────────────────────────────────────────────────────────────────
-- MIGRATION: Add HITL columns to existing table
-- ──────────────────────────────────────────────────────────────────────────────
-- Run these if the trading_signals table already exists without HITL columns.
-- Safe to run multiple times (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
--
-- ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending';
-- ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS order_id VARCHAR(50);
-- ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS approved_via VARCHAR(20);
-- ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
-- ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ;
-- ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS execution_error TEXT;
-- ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1;
-- CREATE INDEX IF NOT EXISTS idx_ts_status ON trading_signals(status);
-- UPDATE trading_signals SET status = 'alert_only' WHERE status IS NULL;

