/* ─── Supabase Database Types ─── */

export interface TradingSignal {
  id: number;
  created_at: string;
  signal_time: string;
  symbol: string;
  direction: "BUY" | "SELL";
  signal_type: string;
  trigger_price: number;
  target_price: number | null;
  stop_loss: number;
  wobi_ratio: number;
  volume_spike: number | null;
  atr_1m: number | null;
  total_bid_qty: number;
  total_ask_qty: number;
  trend: string;
  or_high: number | null;
  or_low: number | null;
  metadata: Record<string, unknown>;
  status: "pending" | "approved" | "executing" | "executed" | "rejected" | "failed" | "expired" | "alert_only";
  order_id: string | null;
  approved_via: "telegram" | "dashboard" | null;
  approved_at: string | null;
  executed_at: string | null;
  execution_error: string | null;
  quantity: number;
}

export interface Database {
  public: {
    Tables: {
      trading_signals: {
        Row: TradingSignal;
        Insert: Omit<TradingSignal, "id" | "created_at">;
        Update: Partial<Omit<TradingSignal, "id">>;
        Relationships: [];
      };
    };
  };
}
