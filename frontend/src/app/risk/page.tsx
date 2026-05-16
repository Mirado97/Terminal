"use client";
import { useTerminalState } from "@/hooks/useTerminalState";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { fmtPct, fmtUsdt } from "@/lib/format";

function Row({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-terminal-border/40 last:border-0">
      <span className="text-xs text-terminal-muted">{label}</span>
      <span className={`text-xs font-semibold ${warn ? "text-terminal-red" : "text-gray-200"}`}>{value}</span>
    </div>
  );
}

export default function RiskPage() {
  const { risk, inventory } = useTerminalState();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-bold text-gray-200 uppercase tracking-widest">Risk Monitor</h1>
        {risk && (
          <Badge
            label={risk.halted ? "HALTED" : risk.paused ? "PAUSED" : "ACTIVE"}
            variant={risk.halted ? "red" : risk.paused ? "yellow" : "green"}
            dot
          />
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <Card title="Circuit Breaker">
          {risk ? (
            <>
              <Row label="Status"      value={risk.halted ? "EMERGENCY STOP" : risk.paused ? "PAUSED" : "TRADING"} warn={risk.halted} />
              <Row label="Pause reason" value={risk.pause_reason || "—"} />
              <Row label="Violations"  value={String(risk.violations)} warn={risk.violations > 0} />
            </>
          ) : <span className="text-terminal-muted text-xs">нет данных</span>}
        </Card>

        <Card title="Exposure & PnL">
          {risk ? (
            <>
              <Row label="Realized PnL"   value={`${risk.realized_pnl >= 0 ? "+" : ""}${risk.realized_pnl.toFixed(4)} USDT`} />
              <Row label="Drawdown"        value={fmtPct(risk.drawdown_pct)} warn={risk.drawdown_pct > 5} />
              <Row label="Total exposure"  value={fmtUsdt(risk.total_exposure_usdt)} />
              <Row label="Trade count"     value={String(risk.trade_count)} />
            </>
          ) : <span className="text-terminal-muted text-xs">нет данных</span>}
        </Card>
      </div>

      <Card title="Positions">
        {inventory && Object.keys(inventory.positions).length > 0 ? (
          <div className="space-y-0">
            {Object.entries(inventory.positions).map(([key, pos]) => (
              <Row
                key={key}
                label={key}
                value={`${pos.qty.toFixed(6)} qty | exp ${fmtUsdt(pos.exposure)}`}
                warn={pos.exposure > 1000}
              />
            ))}
          </div>
        ) : (
          <div className="text-terminal-muted text-xs">нет открытых позиций</div>
        )}
      </Card>
    </div>
  );
}
