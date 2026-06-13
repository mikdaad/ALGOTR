"use client";

import { TradingSignal } from "@/types/database";
import { useEffect, useState } from "react";

interface TickerBannerProps {
  signal: TradingSignal | null;
}

export function TickerBanner({ signal }: TickerBannerProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (signal) {
      setVisible(true);
    } else {
      setVisible(false);
    }
  }, [signal]);

  if (!signal || !visible) {
    return (
      <div className="relative overflow-hidden rounded-2xl border border-white/[0.06] bg-[#0c0c14]/80 backdrop-blur-xl p-6">
        <div className="flex items-center justify-center gap-3 text-white/30">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan-400/60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-cyan-500" />
          </span>
          <span className="text-sm font-mono tracking-wider uppercase">
            Monitoring — Awaiting Next Signal
          </span>
        </div>
      </div>
    );
  }

  const isBuy = signal.direction === "BUY";
  const borderColor = isBuy ? "border-emerald-500/60" : "border-red-500/60";
  const glowColor = isBuy
    ? "shadow-[0_0_40px_rgba(16,185,129,0.25)]"
    : "shadow-[0_0_40px_rgba(239,68,68,0.25)]";
  const bgGradient = isBuy
    ? "from-emerald-500/10 via-emerald-500/5 to-transparent"
    : "from-red-500/10 via-red-500/5 to-transparent";
  const textColor = isBuy ? "text-emerald-400" : "text-red-400";
  const iconColor = isBuy ? "bg-emerald-500" : "bg-red-500";
  const signalLabel = signal.signal_type.includes("VELOCITY")
    ? "⚡ 3-POINT VELOCITY"
    : "📊 BREAKOUT";

  return (
    <div
      className={`
        relative overflow-hidden rounded-2xl border-2 ${borderColor} ${glowColor}
        bg-gradient-to-r ${bgGradient} backdrop-blur-xl
        animate-[fadeSlideIn_0.4s_ease-out]
        transition-all duration-300
      `}
    >
      {/* Animated scan line */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div
          className={`
            absolute top-0 left-0 w-full h-[2px]
            ${isBuy ? "bg-gradient-to-r from-transparent via-emerald-400 to-transparent" : "bg-gradient-to-r from-transparent via-red-400 to-transparent"}
            animate-[scanline_2s_linear_infinite]
          `}
        />
      </div>

      <div className="relative p-5">
        <div className="flex items-center justify-between">
          {/* Left — Direction + Symbol */}
          <div className="flex items-center gap-4">
            <div className={`flex items-center gap-2 px-4 py-2 rounded-lg ${iconColor}/20`}>
              <span className={`inline-block w-3 h-3 rounded-full ${iconColor} animate-pulse`} />
              <span className={`text-lg font-black tracking-wide ${textColor}`}>
                {signal.direction}
              </span>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-2xl font-black text-white tracking-tight">
                  {signal.symbol}
                </span>
                <span className="px-2 py-0.5 rounded-md text-[10px] font-bold tracking-wider uppercase bg-white/[0.06] text-white/50">
                  {signalLabel}
                </span>
              </div>
              <span className="text-xs text-white/40 font-mono">
                {new Date(signal.signal_time).toLocaleTimeString("en-IN", {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </span>
            </div>
          </div>

          {/* Right — Key Metrics */}
          <div className="flex items-center gap-6">
            <MetricPill label="Entry" value={`₹${signal.trigger_price}`} color={textColor} />
            {signal.target_price && (
              <MetricPill
                label="Target"
                value={`₹${signal.target_price}`}
                color="text-emerald-400"
              />
            )}
            <MetricPill label="Stop" value={`₹${signal.stop_loss}`} color="text-red-400" />
            <MetricPill
              label="WOBI"
              value={`${signal.wobi_ratio > 0 ? "+" : ""}${signal.wobi_ratio.toFixed(2)}`}
              color={signal.wobi_ratio > 0 ? "text-emerald-400" : "text-red-400"}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricPill({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div className="text-center">
      <div className="text-[10px] font-medium uppercase tracking-wider text-white/30 mb-0.5">
        {label}
      </div>
      <div className={`text-base font-bold font-mono ${color}`}>{value}</div>
    </div>
  );
}
