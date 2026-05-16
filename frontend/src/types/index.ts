// Общие типы для WebSocket протокола и API ответов

export interface InventorySnapshot {
  realized_pnl: number;
  peak_pnl: number;
  drawdown_pct: number;
  total_exposure_usdt: number;
  trade_count: number;
  positions: Record<string, { qty: number; exposure: number }>;
}

export interface RiskStatus {
  halted: boolean;
  paused: boolean;
  pause_reason: string;
  drawdown_pct: number;
  realized_pnl: number;
  total_exposure_usdt: number;
  trade_count: number;
  violations: number;
}

export interface WorkerSnapshot {
  symbol: string;
  state: "running" | "failed" | "disabled" | "stopped" | "starting";
  restarts: number;
  opportunities_seen: number;
  trades_executed: number;
  trades_skipped: number;
  heartbeat_age_s: number;
  last_error: string;
}

export interface WatchdogStatus {
  total: number;
  running: number;
  failed: number;
  disabled: number;
  stopped: number;
  starting: number;
  routed_opportunities: number;
  missed_opportunities: number;
  workers: WorkerSnapshot[];
}

export interface LatencyStats {
  p50: number;
  p95: number;
  p99: number;
  count: number;
}

export interface LatencySnapshot {
  execution: LatencyStats;
  [exchange: string]: LatencyStats | { ws: LatencyStats; rest: LatencyStats };
}

export interface WsSnapshot {
  type: "snapshot";
  ts: number;
  data: {
    inventory?: InventorySnapshot;
    risk?: RiskStatus;
    watchdog?: WatchdogStatus;
    latency?: { execution: LatencyStats };
  };
}

export type WsMessage = WsSnapshot | { type: "pong" };

export interface TradeRecord {
  id: number;
  symbol: string;
  buy_exchange: string;
  sell_exchange: string;
  pnl_usdt: number;
  realized_spread_bps: number;
  fee_usdt: number;
  state: "completed" | "failed";
  execution_time_ms: number;
  created_at: string;
}

export interface SpreadRecord {
  id: number;
  symbol: string;
  buy_exchange: string;
  sell_exchange: string;
  raw_spread_bps: number;
  executable_spread_bps: number;
  executed: boolean;
  created_at: string;
}
