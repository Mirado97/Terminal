"use client";
import type { WorkerSnapshot, WatchdogStatus } from "@/types";
import { clsx } from "clsx";
import { stateColor, fmtAge } from "@/lib/format";

function WorkerCard({ w }: { w: WorkerSnapshot }) {
  const isAlive = w.state === "running";
  return (
    <div className={clsx(
      "border rounded p-2 text-xs transition-colors",
      w.state === "running"  ? "border-terminal-accent/20 bg-terminal-accent/5" :
      w.state === "failed"   ? "border-terminal-red/30 bg-terminal-red/5" :
      w.state === "disabled" ? "border-gray-800 opacity-50" :
                               "border-terminal-border",
    )}>
      <div className="flex items-center justify-between mb-1">
        <span className="font-semibold text-gray-200">{w.symbol}</span>
        <span className={clsx("text-xs", stateColor(w.state))}>{w.state}</span>
      </div>
      <div className="grid grid-cols-2 gap-x-2 text-terminal-muted">
        <span>trades: <span className="text-gray-300">{w.trades_executed}</span></span>
        <span>skip: <span className="text-gray-300">{w.trades_skipped}</span></span>
        <span>restart: <span className={w.restarts > 0 ? "text-terminal-yellow" : "text-gray-300"}>{w.restarts}</span></span>
        <span>hb: <span className={w.heartbeat_age_s > 10 ? "text-terminal-red" : "text-gray-300"}>
          {fmtAge(w.heartbeat_age_s)}
        </span></span>
      </div>
      {w.last_error && (
        <div className="mt-1 text-terminal-red truncate" title={w.last_error}>
          {w.last_error}
        </div>
      )}
    </div>
  );
}

interface Props {
  watchdog: WatchdogStatus | null;
}

export function WorkerGrid({ watchdog }: Props) {
  if (!watchdog) {
    return <div className="text-terminal-muted text-xs text-center py-8">нет данных</div>;
  }

  const { workers, running, failed, disabled, routed_opportunities, missed_opportunities } = watchdog;

  return (
    <div className="space-y-4">
      {/* Сводка */}
      <div className="grid grid-cols-5 gap-3">
        {[
          { label: "TOTAL",   value: watchdog.total,  color: "text-gray-200" },
          { label: "RUNNING", value: running,          color: "text-terminal-accent" },
          { label: "FAILED",  value: failed,           color: "text-terminal-red" },
          { label: "DISABLED",value: disabled,         color: "text-gray-500" },
          { label: "ROUTED",  value: routed_opportunities, color: "text-blue-400" },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-terminal-panel border border-terminal-border rounded p-2 text-center">
            <div className="text-terminal-muted text-xs mb-1">{label}</div>
            <div className={clsx("text-lg font-bold", color)}>{value}</div>
          </div>
        ))}
      </div>

      {/* Сетка воркеров */}
      {workers.length > 0 ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2">
          {workers.map((w) => <WorkerCard key={w.symbol} w={w} />)}
        </div>
      ) : (
        <div className="text-terminal-muted text-xs text-center py-8">
          воркеры не зарегистрированы
        </div>
      )}
    </div>
  );
}
