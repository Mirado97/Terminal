// Утилиты форматирования чисел и дат

export function fmtPnl(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)} USDT`;
}

export function fmtBps(v: number): string {
  return `${(v / 100).toFixed(2)}%`;
}

export function fmtUsdt(v: number): string {
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function fmtPct(v: number): string {
  return `${v.toFixed(3)}%`;
}

export function fmtMs(v: number): string {
  return `${v.toFixed(0)}ms`;
}

export function fmtUs(v: number): string {
  return v >= 1000 ? `${(v / 1000).toFixed(2)}ms` : `${v}µs`;
}

export function fmtTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("ru-RU", { hour12: false });
}

export function fmtAge(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(0)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export function pnlColor(v: number): string {
  if (v > 0) return "text-terminal-accent";
  if (v < 0) return "text-terminal-red";
  return "text-gray-400";
}

export function stateColor(state: string): string {
  switch (state) {
    case "running":  return "text-terminal-accent";
    case "failed":   return "text-terminal-red";
    case "disabled": return "text-gray-600";
    case "starting": return "text-terminal-yellow";
    default:         return "text-gray-400";
  }
}
