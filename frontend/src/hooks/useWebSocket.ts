"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { WsMessage } from "@/types";

type Status = "connecting" | "connected" | "disconnected" | "error";

interface UseWebSocketReturn {
  lastMessage: WsMessage | null;
  status: Status;
  reconnectCount: number;
}

const BASE_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS  = 30_000;

export function useWebSocket(url: string): UseWebSocketReturn {
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [reconnectCount, setReconnectCount] = useState(0);

  const wsRef     = useRef<WebSocket | null>(null);
  const timerRef  = useRef<ReturnType<typeof setTimeout> | null>(null);
  const countRef  = useRef(0);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    setStatus("connecting");

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return; }
      countRef.current = 0;
      setReconnectCount(0);
      setStatus("connected");
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const msg: WsMessage = JSON.parse(event.data as string);
        setLastMessage(msg);
      } catch { /* ignore malformed */ }
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setStatus("disconnected");
      scheduleReconnect();
    };

    ws.onerror = () => {
      setStatus("error");
      ws.close();
    };
  }, [url]);

  const scheduleReconnect = useCallback(() => {
    const delay = Math.min(BASE_BACKOFF_MS * 2 ** countRef.current, MAX_BACKOFF_MS);
    countRef.current += 1;
    setReconnectCount(countRef.current);
    timerRef.current = setTimeout(() => {
      if (mountedRef.current) connect();
    }, delay);
  }, [connect]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { lastMessage, status, reconnectCount };
}
