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
