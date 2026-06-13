"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { supabase } from "@/lib/supabase";
import { playSignalAlert } from "@/lib/audio";
import type { TradingSignal } from "@/types/database";
import type { RealtimeChannel } from "@supabase/supabase-js";

interface UseLiveSignalsReturn {
  /** All signals loaded today (most recent first) */
  signals: TradingSignal[];
  /** The latest signal that just arrived (for flash banner) */
  latestSignal: TradingSignal | null;
  /** Whether the initial load is still in progress */
  isLoading: boolean;
  /** Connection status: 'connecting' | 'connected' | 'disconnected' */
  connectionStatus: "connecting" | "connected" | "disconnected";
  /** Total signal count today */
  totalCount: number;
  /** Count of BUY signals */
  buyCount: number;
  /** Count of SELL signals */
  sellCount: number;
  /** Whether audio alerts are enabled */
  audioEnabled: boolean;
  /** Toggle audio alerts */
  toggleAudio: () => void;
}

export function useLiveSignals(): UseLiveSignalsReturn {
  const [signals, setSignals] = useState<TradingSignal[]>([]);
  const [latestSignal, setLatestSignal] = useState<TradingSignal | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [connectionStatus, setConnectionStatus] = useState<
    "connecting" | "connected" | "disconnected"
  >("connecting");
  const [audioEnabled, setAudioEnabled] = useState(true);

  const channelRef = useRef<RealtimeChannel | null>(null);
  const latestTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Fetch today's signals on mount ──
  const fetchTodaySignals = useCallback(async () => {
    setIsLoading(true);
    try {
      const todayStart = new Date();
      todayStart.setHours(0, 0, 0, 0);

      const { data, error } = await supabase
        .from("trading_signals")
        .select("*")
        .gte("signal_time", todayStart.toISOString())
        .order("signal_time", { ascending: false })
        .limit(500);

      if (error) {
        console.error("[useLiveSignals] Fetch error:", error);
      } else if (data) {
        setSignals(data as TradingSignal[]);
      }
    } catch (err) {
      console.error("[useLiveSignals] Unexpected error:", err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // ── Subscribe to realtime INSERTs ──
  useEffect(() => {
    fetchTodaySignals();

    const channel = supabase
      .channel("live-trading-signals")
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "trading_signals",
        },
        (payload) => {
          if (payload.eventType === "INSERT") {
            const newSignal = payload.new as TradingSignal;

            // Prepend to list (most recent first)
            setSignals((prev) => [newSignal, ...prev]);

            // Set as latest for the flash banner
            setLatestSignal(newSignal);

            // Clear previous banner timer
            if (latestTimerRef.current) {
              clearTimeout(latestTimerRef.current);
            }

            // Auto-dismiss banner after 8 seconds
            latestTimerRef.current = setTimeout(() => {
              setLatestSignal(null);
            }, 8000);

            // Play audio alert
            if (audioEnabled) {
              playSignalAlert(newSignal.direction);
            }
          } else if (payload.eventType === "UPDATE") {
            const updatedSignal = payload.new as TradingSignal;
            setSignals((prev) =>
              prev.map((s) => (s.id === updatedSignal.id ? updatedSignal : s))
            );
          }
        }
      )
      .subscribe((status) => {
        if (status === "SUBSCRIBED") {
          setConnectionStatus("connected");
        } else if (status === "CLOSED" || status === "CHANNEL_ERROR") {
          setConnectionStatus("disconnected");
        }
      });

    channelRef.current = channel;

    return () => {
      if (latestTimerRef.current) clearTimeout(latestTimerRef.current);
      if (channelRef.current) supabase.removeChannel(channelRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Audio toggle (separate effect to avoid re-subscribing)
  const toggleAudio = useCallback(() => {
    setAudioEnabled((prev) => !prev);
  }, []);

  // Derived counts
  const buyCount = signals.filter((s) => s.direction === "BUY").length;
  const sellCount = signals.filter((s) => s.direction === "SELL").length;

  return {
    signals,
    latestSignal,
    isLoading,
    connectionStatus,
    totalCount: signals.length,
    buyCount,
    sellCount,
    audioEnabled,
    toggleAudio,
  };
}
