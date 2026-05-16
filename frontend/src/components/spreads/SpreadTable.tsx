"use client";
import type { SpreadRecord } from "@/types";
import { clsx } from "clsx";
import { fmtBps, fmtUsdt, fmtTime } from "@/lib/format";

interface Props {
  spreads: SpreadRecord[];
  loading?: boolean;
}

export function SpreadTable({ spreads, loading }: Props) {
  if (loading) {
    return <div className="text-terminal-muted text-xs text-center py-8 animate-pulse">загрузка...</div>;
  }
  if (spreads.length === 0) {
    return (
      <div className="text-terminal-muted text-xs text-center py-8">
        нет данных — spreads появятся после первого сканирования
      </div>
    );
  }

  return (
    <div className="overflow-auto">
      <table className="w-full text-xs text-left">
        <thead>
          <tr className="border-b border-terminal-border text-terminal-muted uppercase tracking-wider">
            <th className="pb-2 pr-4">Пара</th>
            <th className="pb-2 pr-4">Купить на</th>
            <th className="pb-2 pr-4">Продать на</th>
            <th className="pb-2 pr-4 text-right">Разница цен</th>
            <th className="pb-2 pr-4 text-right">Чистая прибыль</th>
            <th className="pb-2 pr-4">Исполнено</th>
            <th className="pb-2">Время</th>
          </tr>
        </thead>
        <tbody>
          {spreads.map((s) => (
            <tr
              key={`${s.symbol}-${s.buy_exchange}-${s.sell_exchange}`}
              className="border-b border-terminal-border/40 hover:bg-white/2 transition-colors"
            >
              <td className="py-1.5 pr-4 font-semibold text-gray-200">{s.symbol}</td>
              <td className="py-1.5 pr-4 text-blue-400">{s.buy_exchange}</td>
              <td className="py-1.5 pr-4 text-purple-400">{s.sell_exchange}</td>
              <td className="py-1.5 pr-4 text-right text-gray-400">{fmtBps(s.raw_spread_bps)}</td>
              <td className={clsx(
                "py-1.5 pr-4 text-right font-semibold",
                s.executable_spread_bps >= 10 ? "text-terminal-accent glow-green" : "text-gray-300",
              )}>
                {fmtBps(s.executable_spread_bps)}
              </td>
              <td className="py-1.5 pr-4">
                {s.executed
                  ? <span className="text-terminal-accent">✓</span>
                  : <span className="text-terminal-muted">—</span>}
              </td>
              <td className="py-1.5 text-terminal-muted">{s.created_at?.slice(-8) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
