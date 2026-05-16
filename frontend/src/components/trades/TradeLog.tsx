"use client";
import type { TradeRecord } from "@/types";
import { clsx } from "clsx";
import { fmtPnl, fmtBps, fmtMs, fmtTime, pnlColor } from "@/lib/format";

interface Props {
  trades: TradeRecord[];
  loading?: boolean;
}

export function TradeLog({ trades, loading }: Props) {
  if (loading) {
    return <div className="text-terminal-muted text-xs text-center py-8 animate-pulse">загрузка...</div>;
  }
  if (trades.length === 0) {
    return (
      <div className="text-terminal-muted text-xs text-center py-8">
        нет сделок — они появятся после первого исполнения
      </div>
    );
  }

  return (
    <div className="overflow-auto">
      <table className="w-full text-xs text-left">
        <thead>
          <tr className="border-b border-terminal-border text-terminal-muted uppercase tracking-wider">
            <th className="pb-2 pr-4">Symbol</th>
            <th className="pb-2 pr-4">Buy</th>
            <th className="pb-2 pr-4">Sell</th>
            <th className="pb-2 pr-4 text-right">PnL</th>
            <th className="pb-2 pr-4 text-right">Spread</th>
            <th className="pb-2 pr-4 text-right">Fee</th>
            <th className="pb-2 pr-4 text-right">Exec</th>
            <th className="pb-2 pr-4">State</th>
            <th className="pb-2">Time</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr
              key={t.id}
              className="border-b border-terminal-border/40 hover:bg-white/2 transition-colors"
            >
              <td className="py-1.5 pr-4 font-semibold text-gray-200">{t.symbol}</td>
              <td className="py-1.5 pr-4 text-blue-400">{t.buy_exchange}</td>
              <td className="py-1.5 pr-4 text-purple-400">{t.sell_exchange}</td>
              <td className={clsx("py-1.5 pr-4 text-right font-semibold", pnlColor(t.pnl_usdt))}>
                {fmtPnl(t.pnl_usdt)}
              </td>
              <td className="py-1.5 pr-4 text-right text-gray-400">{fmtBps(t.realized_spread_bps)}</td>
              <td className="py-1.5 pr-4 text-right text-terminal-red">-{t.fee_usdt.toFixed(4)}</td>
              <td className="py-1.5 pr-4 text-right text-gray-400">{fmtMs(t.execution_time_ms)}</td>
              <td className="py-1.5 pr-4">
                <span className={t.state === "completed" ? "text-terminal-accent" : "text-terminal-red"}>
                  {t.state}
                </span>
              </td>
              <td className="py-1.5 text-terminal-muted">{fmtTime(new Date(t.created_at).getTime())}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
