"use client";

import { TradingSignal } from "@/types/database";

interface SignalTableProps {
  signals: TradingSignal[];
  isLoading: boolean;
}

export function SignalTable({ signals, isLoading }: SignalTableProps) {
  if (isLoading) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-[#0c0c14]/60 backdrop-blur-xl p-8">
        <div className="flex items-center justify-center gap-3 text-white/30">
          <div className="w-4 h-4 border-2 border-white/20 border-t-cyan-500 rounded-full animate-spin" />
          <span className="text-sm font-mono">Loading today&apos;s signals...</span>
        </div>
      </div>
    );
  }

  if (signals.length === 0) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-[#0c0c14]/60 backdrop-blur-xl p-8 text-center">
        <span className="text-white/20 text-sm font-mono">
          No signals generated today yet.
        </span>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-[#0c0c14]/60 backdrop-blur-xl overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/[0.06]">
              <Th>Time</Th>
              <Th>Symbol</Th>
              <Th>Dir</Th>
              <Th>Type</Th>
              <Th align="right">Entry</Th>
              <Th align="right">Target</Th>
              <Th align="right">Stop</Th>
              <Th align="right">WOBI</Th>
              <Th align="right">Vol×</Th>
              <Th>Trend</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>
            {signals.map((signal, idx) => (
              <SignalRow key={signal.id} signal={signal} isNew={idx === 0} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SignalRow({
  signal,
  isNew,
}: {
  signal: TradingSignal;
  isNew: boolean;
}) {
  const isBuy = signal.direction === "BUY";
  const dirColor = isBuy ? "text-emerald-400" : "text-red-400";
  const dirBg = isBuy ? "bg-emerald-500/10" : "bg-red-500/10";
  const wobiColor =
    signal.wobi_ratio > 0 ? "text-emerald-400" : "text-red-400";
  const isVelocity = signal.signal_type.includes("VELOCITY");

  return (
    <tr
      className={`
        border-b border-white/[0.03] transition-colors duration-200
        hover:bg-white/[0.02]
        ${isNew ? "animate-[rowFlash_1.5s_ease-out]" : ""}
      `}
    >
      <Td mono>
        {new Date(signal.signal_time).toLocaleTimeString("en-IN", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })}
      </Td>
      <Td>
        <span className="font-bold text-white">{signal.symbol}</span>
      </Td>
      <Td>
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold ${dirColor} ${dirBg}`}
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${isBuy ? "bg-emerald-500" : "bg-red-500"}`}
          />
          {signal.direction}
        </span>
      </Td>
      <Td>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.04] text-white/40 font-bold tracking-wider uppercase">
          {isVelocity ? "⚡ VEL" : "📊 BRK"}
        </span>
      </Td>
      <Td align="right" mono>
        ₹{signal.trigger_price.toFixed(2)}
      </Td>
      <Td align="right" mono>
        {signal.target_price ? (
          <span className="text-emerald-400/70">
            ₹{signal.target_price.toFixed(2)}
          </span>
        ) : (
          <span className="text-white/15">—</span>
        )}
      </Td>
      <Td align="right" mono>
        <span className="text-red-400/70">₹{signal.stop_loss.toFixed(2)}</span>
      </Td>
      <Td align="right" mono>
        <span className={wobiColor}>
          {signal.wobi_ratio > 0 ? "+" : ""}
          {signal.wobi_ratio.toFixed(4)}
        </span>
      </Td>
      <Td align="right" mono>
        {signal.volume_spike ? (
          <span className="text-cyan-400/70">
            {signal.volume_spike.toFixed(1)}×
          </span>
        ) : (
          <span className="text-white/15">—</span>
        )}
      </Td>
      <Td>
        {signal.trend !== "N/A" ? (
          <span
            className={`text-[10px] font-bold ${
              signal.trend === "ABOVE"
                ? "text-emerald-500/60"
                : "text-red-500/60"
            }`}
          >
            {signal.trend}
          </span>
        ) : (
          <span className="text-white/15">—</span>
        )}
      </Td>
      <Td>
        {getStatusBadge(signal)}
      </Td>
    </tr>
  );
}

function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      className={`
        px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-white/30
        ${align === "right" ? "text-right" : "text-left"}
      `}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align = "left",
  mono = false,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  mono?: boolean;
}) {
  return (
    <td
      className={`
        px-4 py-3 text-white/60
        ${align === "right" ? "text-right" : "text-left"}
        ${mono ? "font-mono text-xs" : ""}
      `}
    >
      {children}
    </td>
  );
}

function getStatusBadge(signal: TradingSignal) {
  const status = signal.status;
  if (!status || status === "alert_only") {
    return <span className="text-white/20">—</span>;
  }

  switch (status) {
    case "pending":
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20">
          PENDING
        </span>
      );
    case "approved":
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-yellow-500/10 text-yellow-400 border border-yellow-500/20">
          APPROVED
        </span>
      );
    case "executing":
      return (
        <span className="inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] font-bold bg-yellow-500/20 text-yellow-300 border border-yellow-500/30 animate-pulse">
          <span className="w-1 h-1 rounded-full bg-yellow-400 animate-ping" />
          RUNNING
        </span>
      );
    case "executed":
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
          EXECUTED
        </span>
      );
    case "rejected":
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-white/[0.04] text-white/40 border border-white/[0.06]">
          REJECTED
        </span>
      );
    case "failed":
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-500/10 text-red-400 border border-red-500/20">
          FAILED
        </span>
      );
    case "expired":
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-white/[0.02] text-white/30 border border-white/[0.04]">
          EXPIRED
        </span>
      );
    default:
      return <span className="text-white/20">—</span>;
  }
}
