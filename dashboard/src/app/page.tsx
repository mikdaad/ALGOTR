"use client";

import { useLiveSignals } from "@/hooks/useLiveSignals";
import { TickerBanner } from "@/components/TickerBanner";
import { SignalCards } from "@/components/SignalCards";
import { SignalTable } from "@/components/SignalTable";

export default function DashboardPage() {
  const {
    signals,
    latestSignal,
    isLoading,
    connectionStatus,
    totalCount,
    buyCount,
    sellCount,
    audioEnabled,
    toggleAudio,
  } = useLiveSignals();

  return (
    <main className="flex-1 flex flex-col">
      {/* ── Header Bar ── */}
      <header className="sticky top-0 z-50 backdrop-blur-2xl bg-[#050508]/80 border-b border-white/[0.04]">
        <div className="max-w-[1600px] mx-auto px-6 py-4 flex items-center justify-between">
          {/* Left — Logo + Title */}
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center">
                <svg
                  className="w-4 h-4 text-white"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2.5}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
                  />
                </svg>
              </div>
              <div>
                <h1 className="text-base font-black text-white tracking-tight">
                  Signal Terminal
                </h1>
                <p className="text-[10px] text-white/30 font-mono tracking-wider uppercase">
                  Live Trading Dashboard
                </p>
              </div>
            </div>
          </div>

          {/* Right — Status indicators */}
          <div className="flex items-center gap-5">
            {/* Stats pills */}
            <div className="hidden sm:flex items-center gap-3">
              <StatPill label="Total" value={totalCount} color="text-cyan-400" />
              <StatPill label="Buy" value={buyCount} color="text-emerald-400" />
              <StatPill label="Sell" value={sellCount} color="text-red-400" />
            </div>

            {/* Divider */}
            <div className="w-px h-6 bg-white/[0.06]" />

            {/* Audio toggle */}
            <button
              onClick={toggleAudio}
              className={`
                flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                transition-all duration-200 cursor-pointer
                ${
                  audioEnabled
                    ? "bg-cyan-500/10 text-cyan-400 hover:bg-cyan-500/20"
                    : "bg-white/[0.04] text-white/30 hover:bg-white/[0.08]"
                }
              `}
              title={audioEnabled ? "Mute audio alerts" : "Enable audio alerts"}
            >
              {audioEnabled ? (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.536 8.464a5 5 0 010 7.072M17.95 6.05a8 8 0 010 11.9M11 5L6 9H2v6h4l5 4V5z" />
                </svg>
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M17 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2" />
                </svg>
              )}
              <span>{audioEnabled ? "ON" : "OFF"}</span>
            </button>

            {/* Connection status */}
            <div className="flex items-center gap-1.5">
              <span
                className={`w-2 h-2 rounded-full ${
                  connectionStatus === "connected"
                    ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)]"
                    : connectionStatus === "connecting"
                      ? "bg-amber-500 animate-pulse"
                      : "bg-red-500"
                }`}
              />
              <span className="text-[10px] text-white/25 font-mono uppercase tracking-wider">
                {connectionStatus === "connected"
                  ? "Live"
                  : connectionStatus === "connecting"
                    ? "Connecting"
                    : "Offline"}
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* ── Dashboard Content ── */}
      <div className="flex-1 max-w-[1600px] w-full mx-auto px-6 py-6 space-y-6">
        {/* Section 1: Live Ticker Banner */}
        <section>
          <TickerBanner signal={latestSignal} />
        </section>

        {/* Section 2: Active Setup Cards */}
        <section>
          <SectionHeader
            title="Active Setups"
            subtitle={`${Math.min(signals.length, 12)} most recent`}
            icon="⚡"
          />
          <SignalCards signals={signals} />
        </section>

        {/* Section 3: Historical Signal Table */}
        <section>
          <SectionHeader
            title="Today&apos;s Signal Log"
            subtitle={`${totalCount} signals`}
            icon="📋"
          />
          <SignalTable signals={signals} isLoading={isLoading} />
        </section>
      </div>

      {/* ── Footer ── */}
      <footer className="border-t border-white/[0.04] py-4">
        <div className="max-w-[1600px] mx-auto px-6 flex items-center justify-between">
          <span className="text-[10px] text-white/15 font-mono">
            Alert-only · No auto-trading · Execute manually
          </span>
          <span className="text-[10px] text-white/15 font-mono">
            {new Date().toLocaleDateString("en-IN", {
              weekday: "short",
              day: "2-digit",
              month: "short",
              year: "numeric",
            })}
          </span>
        </div>
      </footer>
    </main>
  );
}

function SectionHeader({
  title,
  subtitle,
  icon,
}: {
  title: string;
  subtitle: string;
  icon: string;
}) {
  return (
    <div className="flex items-center justify-between mb-4">
      <div className="flex items-center gap-2">
        <span className="text-base">{icon}</span>
        <h2 className="text-sm font-bold text-white/80 tracking-tight">
          {title}
        </h2>
      </div>
      <span className="text-[10px] text-white/20 font-mono">{subtitle}</span>
    </div>
  );
}

function StatPill({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white/[0.03]">
      <span className="text-[10px] text-white/30 uppercase tracking-wider font-medium">
        {label}
      </span>
      <span className={`text-xs font-bold font-mono ${color}`}>{value}</span>
    </div>
  );
}
