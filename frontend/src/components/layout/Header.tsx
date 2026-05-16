"use client";
import { useTerminalState } from "@/hooks/useTerminalState";
import { StatusDot } from "@/components/ui/StatusDot";
import { fmtTime } from "@/lib/format";

export function Header() {
  const { wsStatus, lastUpdated, risk } = useTerminalState();

  const dot = wsStatus === "connected" ? "ok"
            : wsStatus === "connecting" ? "warn"
            : "error";

  return (
    <header className="h-10 bg-terminal-panel border-b border-terminal-border px-4
                       flex items-center justify-between shrink-0">
      <div className="flex items-center gap-4">
        <StatusDot status={dot} label={wsStatus} />
        {risk?.halted && (
          <span className="text-xs text-terminal-red font-bold animate-pulse">
            ⛔ EMERGENCY STOP
          </span>
        )}
        {risk?.paused && !risk.halted && (
          <span className="text-xs text-terminal-yellow">⏸ PAUSED</span>
        )}
      </div>

      <div className="flex items-center gap-6 text-xs text-terminal-muted">
        {lastUpdated && (
          <span>upd {fmtTime(lastUpdated)}</span>
        )}
        <span className="text-gray-600">ARB TERMINAL</span>
      </div>
    </header>
  );
}
