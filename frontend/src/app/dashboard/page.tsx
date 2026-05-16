"use client";
import { useTerminalState } from "@/hooks/useTerminalState";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { PnLChart } from "@/components/charts/PnLChart";
import { clsx } from "clsx";
import { fmtPnl, fmtPct, fmtUsdt, pnlColor } from "@/lib/format";

function MetricRow({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-terminal-border/40 last:border-0">
      <span className="text-xs text-terminal-muted">{label}</span>
      <span className="text-xs font-semibold text-gray-200">
        {value}
        {sub && <span className="text-terminal-muted ml-1">{sub}</span>}
      </span>
    </div>
  );
}

export default function DashboardPage() {
  const { inventory, risk, watchdog, pnlHistory, wsStatus } = useTerminalState();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-bold text-gray-200 uppercase tracking-widest">Dashboard</h1>
        <Badge
          label={wsStatus}
          variant={wsStatus === "connected" ? "green" : wsStatus === "connecting" ? "yellow" : "red"}
          dot
        />
      </div>

      {/* Top row */}
      <div className="grid grid-cols-4 gap-4">
        {/* PnL */}
        <Card title="Realized PnL" accent className="col-span-1">
          <div className={clsx("text-2xl font-bold", pnlColor(inventory?.realized_pnl ?? 0))}>
            {inventory ? fmtPnl(inventory.realized_pnl) : "—"}
          </div>
          <div className="text-xs text-terminal-muted mt-1">
            peak: {inventory ? fmtPnl(inventory.peak_pnl) : "—"}
          </div>
        </Card>

        {/* Drawdown */}
        <Card title="Drawdown">
          <div className={clsx(
            "text-2xl font-bold",
            (inventory?.drawdown_pct ?? 0) > 5 ? "text-terminal-red glow-red" :
            (inventory?.drawdown_pct ?? 0) > 2 ? "text-terminal-yellow" : "text-gray-200",
          )}>
            {inventory ? fmtPct(inventory.drawdown_pct) : "—"}
          </div>
          <div className="text-xs text-terminal-muted mt-1">
            from peak
          </div>
        </Card>

        {/* Exposure */}
        <Card title="Total Exposure">
          <div className="text-2xl font-bold text-gray-200">
            {inventory ? fmtUsdt(inventory.total_exposure_usdt) : "—"}
          </div>
          <div className="text-xs text-terminal-muted mt-1">
            trades: {inventory?.trade_count ?? 0}
          </div>
        </Card>

        {/* Risk status */}
        <Card title="Risk Status">
          {risk ? (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                <span className={clsx("text-lg", risk.halted ? "text-terminal-red" : "text-terminal-accent")}>
                  {risk.halted ? "⛔" : risk.paused ? "⏸" : "✓"}
                </span>
                <span className="text-xs text-gray-300 font-semibold">
                  {risk.halted ? "HALTED" : risk.paused ? "PAUSED" : "TRADING"}
                </span>
              </div>
              <div className="text-xs text-terminal-muted">
                violations: {risk.violations}
              </div>
            </div>
          ) : (
            <span className="text-terminal-muted text-xs">нет данных</span>
          )}
        </Card>
      </div>

      {/* PnL chart */}
      <Card title="PnL History">
        <PnLChart data={pnlHistory} />
      </Card>

      {/* Middle row */}
      <div className="grid grid-cols-2 gap-4">
        {/* Inventory */}
        <Card title="Positions">
          {inventory && Object.keys(inventory.positions).length > 0 ? (
            <div className="space-y-0">
              {Object.entries(inventory.positions).map(([key, pos]) => (
                <MetricRow
                  key={key}
                  label={key}
                  value={`qty ${pos.qty.toFixed(6)}`}
                  sub={`exp ${fmtUsdt(pos.exposure)}`}
                />
              ))}
            </div>
          ) : (
            <div className="text-terminal-muted text-xs">нет открытых позиций</div>
          )}
        </Card>

        {/* Watchdog summary */}
        <Card title="Watchdog">
          {watchdog ? (
            <div className="space-y-0">
              <MetricRow label="Workers total"   value={String(watchdog.total)} />
              <MetricRow label="Running"         value={String(watchdog.running)} />
              <MetricRow label="Failed"          value={String(watchdog.failed)} />
              <MetricRow label="Routed opp"      value={String(watchdog.routed_opportunities)} />
              <MetricRow label="Missed opp"      value={String(watchdog.missed_opportunities)} />
            </div>
          ) : (
            <div className="text-terminal-muted text-xs">нет данных</div>
          )}
        </Card>
      </div>
    </div>
  );
}
