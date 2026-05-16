"use client";
import { useEffect, useRef, useState } from "react";
import type { InventorySnapshot, RiskStatus, WatchdogStatus, LatencyStats } from "@/types";
import { useWebSocket } from "./useWebSocket";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8080/ws";

export interface PnLPoint {
  ts: number;
  pnl: number;
}

export interface TerminalState {
  inventory:    InventorySnapshot | null;
  risk:         RiskStatus | null;
  watchdog:     WatchdogStatus | null;
  execLatency:  LatencyStats | null;
  pnlHistory:   PnLPoint[];
  lastUpdated:  number | null;
  wsStatus:     "connecting" | "connected" | "disconnected" | "error";
  reconnects:   number;
}

const MAX_HISTORY = 300; // 5 минут @ 1s

export function useTerminalState(): TerminalState {
  const { lastMessage, status, reconnectCount } = useWebSocket(WS_URL);
  const [state, setState] = useState<TerminalState>({
    inventory:   null,
    risk:        null,
    watchdog:    null,
    execLatency: null,
    pnlHistory:  [],
    lastUpdated: null,
    wsStatus:    "connecting",
    reconnects:  0,
  });

  useEffect(() => {
    setState(prev => ({ ...prev, wsStatus: status, reconnects: reconnectCount }));
  }, [status, reconnectCount]);

  useEffect(() => {
    if (!lastMessage || lastMessage.type !== "snapshot") return;
    const { data, ts } = lastMessage;

    setState(prev => {
      const newHistory = data.inventory
        ? [...prev.pnlHistory, { ts, pnl: data.inventory!.realized_pnl }].slice(-MAX_HISTORY)
        : prev.pnlHistory;

      return {
        ...prev,
        inventory:   data.inventory   ?? prev.inventory,
        risk:        data.risk        ?? prev.risk,
        watchdog:    data.watchdog    ?? prev.watchdog,
        execLatency: data.latency?.execution ?? prev.execLatency,
        pnlHistory:  newHistory,
        lastUpdated: ts,
      };
    });
  }, [lastMessage]);

  return state;
}
