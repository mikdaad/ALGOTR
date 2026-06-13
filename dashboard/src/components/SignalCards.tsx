"use client";

import { useState } from "react";
import { supabase } from "@/lib/supabase";
import { TradingSignal } from "@/types/database";

interface SignalCardsProps {
  signals: TradingSignal[];
}

export function SignalCards({ signals }: SignalCardsProps) {
  // Show only most recent 12 active setups
  const activeSetups = signals.slice(0, 12);

  if (activeSetups.length === 0) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-[#0c0c14]/60 backdrop-blur-xl p-12 text-center">
        <div className="text-white/20 text-sm font-mono">
          No active setups yet. Signals will appear here in real-time.
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      {activeSetups.map((signal) => (
        <SignalCard key={signal.id} signal={signal} />
      ))}
    </div>
  );
}

function SignalCard({ signal }: { signal: TradingSignal }) {
  const isBuy = signal.direction === "BUY";

  const borderColor = isBuy ? "border-emerald-500/20" : "border-red-500/20";
  const hoverBorder = isBuy
    ? "hover:border-emerald-500/40"
    : "hover:border-red-500/40";
  const accentColor = isBuy ? "text-emerald-400" : "text-red-400";
  const accentBg = isBuy ? "bg-emerald-500/10" : "bg-red-500/10";
  const dotColor = isBuy ? "bg-emerald-500" : "bg-red-500";
  const isVelocity = signal.signal_type.includes("VELOCITY");

  // State hooks for interactive approval form
  const [isConfirming, setIsConfirming] = useState(false);
  const [qty, setQty] = useState(50); // Default to 50 shares
  const [sl, setSl] = useState(signal.stop_loss);
  const [target, setTarget] = useState(
    signal.target_price ||
      (signal.trigger_price + 3.0 * (signal.direction === "BUY" ? 1 : -1))
  );
  const [isSubmitting, setIsSubmitting] = useState(false);

  const riskReward =
    target && sl
      ? Math.abs(target - signal.trigger_price) /
        Math.abs(signal.trigger_price - sl)
      : null;

  const handleReject = async () => {
    setIsSubmitting(true);
    try {
      const { error } = await (supabase as any)
        .from("trading_signals")
        .update({
          status: "rejected",
          approved_via: "dashboard",
          approved_at: new Date().toISOString(),
        })
        .eq("id", signal.id)
        .eq("status", "pending");

      if (error) console.error("Error rejecting:", error);
    } catch (err) {
      console.error("Error rejecting:", err);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleConfirm = async () => {
    if (qty <= 0) {
      alert("Quantity must be greater than 0");
      return;
    }
    setIsSubmitting(true);
    try {
      const { error } = await (supabase as any)
        .from("trading_signals")
        .update({
          status: "approved",
          approved_via: "dashboard",
          quantity: qty,
          stop_loss: sl,
          target_price: target,
          approved_at: new Date().toISOString(),
        })
        .eq("id", signal.id)
        .eq("status", "pending");

      if (error) console.error("Error approving:", error);
    } catch (err) {
      console.error("Error approving:", err);
    } finally {
      setIsSubmitting(false);
      setIsConfirming(false);
    }
  };

  return (
    <div
      className={`
        group relative overflow-hidden rounded-xl border ${borderColor} ${hoverBorder}
        bg-[#0c0c14]/80 backdrop-blur-xl
        transition-all duration-300 hover:translate-y-[-2px]
        hover:shadow-lg
      `}
    >
      {/* Top accent line */}
      <div
        className={`absolute top-0 left-0 right-0 h-[2px] ${
          isBuy
            ? "bg-gradient-to-r from-emerald-500/0 via-emerald-500/80 to-emerald-500/0"
            : "bg-gradient-to-r from-red-500/0 via-red-500/80 to-red-500/0"
        }`}
      />

      <div className="p-4">
        {/* Header: Symbol + Direction badge */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2.5">
            <span className={`w-2 h-2 rounded-full ${dotColor}`} />
            <span className="text-lg font-black text-white tracking-tight">
              {signal.symbol}
            </span>
          </div>
          <div
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md ${accentBg}`}
          >
            <span className={`text-xs font-bold ${accentColor}`}>
              {signal.direction}
            </span>
          </div>
        </div>

        {/* Signal type tag */}
        <div className="mb-4 flex items-center justify-between">
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold tracking-wider uppercase bg-white/[0.04] text-white/40">
            {isVelocity ? "⚡ VELOCITY" : "📊 BREAKOUT"}
          </span>
          {signal.status === "alert_only" && (
            <span className="text-[10px] text-white/20 font-mono">ALERT ONLY</span>
          )}
        </div>

        {/* Metrics grid */}
        <div className="space-y-2.5">
          <MetricRow
            label="Entry Trigger"
            value={`₹${signal.trigger_price.toFixed(2)}`}
            highlight={accentColor}
          />
          {target && (
            <MetricRow
              label="Scalp Target"
              value={`₹${target.toFixed(2)}`}
              sub={`+₹${Math.abs(target - signal.trigger_price).toFixed(2)}`}
              highlight="text-emerald-400"
            />
          )}
          <MetricRow
            label="Stop-Loss"
            value={`₹${sl.toFixed(2)}`}
            sub={`-₹${Math.abs(signal.trigger_price - sl).toFixed(2)}`}
            highlight="text-red-400"
          />

          {/* Divider */}
          <div className="border-t border-white/[0.04] my-2" />

          <MetricRow
            label="WOBI Ratio"
            value={`${signal.wobi_ratio > 0 ? "+" : ""}${signal.wobi_ratio.toFixed(4)}`}
            highlight={signal.wobi_ratio > 0 ? "text-emerald-400" : "text-red-400"}
          />

          {signal.volume_spike && (
            <MetricRow
              label="Vol Surge"
              value={`${signal.volume_spike.toFixed(2)}×`}
              highlight="text-cyan-400"
            />
          )}

          {riskReward && (
            <MetricRow
              label="Risk:Reward"
              value={`1:${riskReward.toFixed(1)}`}
              highlight="text-amber-400"
            />
          )}
        </div>

        {/* HITL Gateway Approval / Status Area */}
        {isVelocity && signal.status === "pending" && (
          <div className="mt-4 pt-3 border-t border-white/[0.04]">
            {isConfirming ? (
              <div className="space-y-3 p-3 rounded-lg bg-white/[0.02] border border-white/[0.05]">
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="block text-[9px] text-white/40 uppercase font-bold mb-1">
                      Qty
                    </label>
                    <input
                      type="number"
                      value={qty}
                      onChange={(e) => setQty(parseInt(e.target.value) || 0)}
                      className="w-full bg-[#12121e] border border-white/[0.1] rounded px-1.5 py-1 text-xs text-white font-mono focus:outline-none focus:border-amber-500/50"
                    />
                  </div>
                  <div>
                    <label className="block text-[9px] text-white/40 uppercase font-bold mb-1">
                      Stop Loss
                    </label>
                    <input
                      type="number"
                      step="0.05"
                      value={sl}
                      onChange={(e) => setSl(parseFloat(e.target.value) || 0)}
                      className="w-full bg-[#12121e] border border-white/[0.1] rounded px-1.5 py-1 text-xs text-white font-mono focus:outline-none focus:border-amber-500/50"
                    />
                  </div>
                  <div>
                    <label className="block text-[9px] text-white/40 uppercase font-bold mb-1">
                      Target
                    </label>
                    <input
                      type="number"
                      step="0.05"
                      value={target}
                      onChange={(e) => setTarget(parseFloat(e.target.value) || 0)}
                      className="w-full bg-[#12121e] border border-white/[0.1] rounded px-1.5 py-1 text-xs text-white font-mono focus:outline-none focus:border-amber-500/50"
                    />
                  </div>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={handleConfirm}
                    disabled={isSubmitting}
                    className="flex-1 bg-gradient-to-r from-emerald-500 to-emerald-600 hover:from-emerald-600 hover:to-emerald-700 text-black text-xs font-bold py-1.5 px-3 rounded transition duration-200 disabled:opacity-50"
                  >
                    {isSubmitting ? "Placing..." : "Confirm"}
                  </button>
                  <button
                    onClick={() => setIsConfirming(false)}
                    disabled={isSubmitting}
                    className="bg-white/[0.06] hover:bg-white/[0.1] text-white text-xs font-medium py-1.5 px-3 rounded transition duration-200"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex gap-2">
                <button
                  onClick={() => setIsConfirming(true)}
                  className="flex-1 bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/30 hover:border-emerald-500/50 text-emerald-400 text-xs font-bold py-1.5 px-3 rounded transition duration-200"
                >
                  Approve
                </button>
                <button
                  onClick={handleReject}
                  disabled={isSubmitting}
                  className="bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 hover:border-red-500/50 text-red-400 text-xs font-bold py-1.5 px-3 rounded transition duration-200 disabled:opacity-50"
                >
                  {isSubmitting ? "..." : "Reject"}
                </button>
              </div>
            )}
          </div>
        )}

        {/* Dynamic Execution Status Badges */}
        {signal.status && signal.status !== "pending" && signal.status !== "alert_only" && (
          <div className="mt-4 pt-3 border-t border-white/[0.04]">
            {signal.status === "approved" && (
              <div className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-amber-500/10 border border-amber-500/20">
                <div className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
                  <span className="text-[10px] font-bold text-amber-400 uppercase tracking-wider">
                    Approved (via {signal.approved_via || "web"})
                  </span>
                </div>
                {signal.quantity > 0 && (
                  <span className="text-[10px] text-amber-400/80 font-mono">
                    Qty: {signal.quantity}
                  </span>
                )}
              </div>
            )}
            {signal.status === "executing" && (
              <div className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-yellow-500/10 border border-yellow-500/20">
                <div className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-yellow-500 animate-ping" />
                  <span className="text-[10px] font-bold text-yellow-400 uppercase tracking-wider">
                    Executing...
                  </span>
                </div>
                <div className="w-3.5 h-3.5 border border-white/20 border-t-yellow-400 rounded-full animate-spin" />
              </div>
            )}
            {signal.status === "executed" && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-emerald-500/10 border border-emerald-500/20">
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                    <span className="text-[10px] font-bold text-emerald-400 uppercase tracking-wider">
                      Executed
                    </span>
                  </div>
                  <span className="text-[10px] text-emerald-400/80 font-mono">
                    {signal.quantity} shares
                  </span>
                </div>
                {signal.order_id && (
                  <div className="text-[10px] text-white/40 font-mono pl-1 flex items-center justify-between">
                    <span>Order: {signal.order_id}</span>
                    {signal.executed_at && (
                      <span>
                        {new Date(signal.executed_at).toLocaleTimeString("en-IN", {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )}
            {signal.status === "rejected" && (
              <div className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-white/[0.03] border border-white/[0.05]">
                <div className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-white/20" />
                  <span className="text-[10px] font-bold text-white/35 uppercase tracking-wider">
                    Rejected ({signal.approved_via || "web"})
                  </span>
                </div>
              </div>
            )}
            {signal.status === "failed" && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-red-500/10 border border-red-500/20">
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                    <span className="text-[10px] font-bold text-red-400 uppercase tracking-wider">
                      Failed
                    </span>
                  </div>
                </div>
                {signal.execution_error && (
                  <div className="text-[9px] text-red-400/70 font-mono pl-1 max-h-12 overflow-y-auto bg-red-950/20 p-1.5 rounded border border-red-900/10">
                    {signal.execution_error}
                  </div>
                )}
              </div>
            )}
            {signal.status === "expired" && (
              <div className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.04]">
                <div className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-white/10" />
                  <span className="text-[10px] font-bold text-white/30 uppercase tracking-wider">
                    Expired
                  </span>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Footer: Timestamp */}
        <div className="mt-4 pt-3 border-t border-white/[0.04]">
          <span className="text-[11px] text-white/25 font-mono">
            {new Date(signal.signal_time).toLocaleTimeString("en-IN", {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            })}
            {" · "}
            {signal.trend !== "N/A" && (
              <span
                className={
                  signal.trend === "ABOVE"
                    ? "text-emerald-500/50"
                    : "text-red-500/50"
                }
              >
                {signal.trend}
              </span>
            )}
          </span>
        </div>
      </div>
    </div>
  );
}

function MetricRow({
  label,
  value,
  sub,
  highlight,
}: {
  label: string;
  value: string;
  sub?: string;
  highlight?: string;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] text-white/30 font-medium uppercase tracking-wider">
        {label}
      </span>
      <div className="flex items-center gap-1.5">
        <span className={`text-sm font-bold font-mono ${highlight || "text-white"}`}>
          {value}
        </span>
        {sub && (
          <span className="text-[10px] text-white/20 font-mono">({sub})</span>
        )}
      </div>
    </div>
  );
}
