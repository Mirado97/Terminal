"use client";
import { useEffect, useState } from "react";
import { TradeLog } from "@/components/trades/TradeLog";
import { Card } from "@/components/ui/Card";
import type { TradeRecord } from "@/types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export default function TradesPage() {
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API}/api/trades?limit=100`);
        const data = await res.json();
        setTrades(data.trades ?? []);
      } catch { /* ignore */ }
      finally { setLoading(false); }
    }
    load();
    const id = setInterval(load, 3_000);
    return () => clearInterval(id);
  }, []);

  const totalPnl = trades.reduce((s, t) => s + t.pnl_usdt, 0);
  const wins = trades.filter(t => t.state === "completed").length;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-bold text-gray-200 uppercase tracking-widest">Execution Log</h1>
        {trades.length > 0 && (
          <div className="flex gap-6 text-xs text-terminal-muted">
            <span>trades: <span className="text-gray-200">{trades.length}</span></span>
            <span>win rate: <span className="text-gray-200">{((wins / trades.length) * 100).toFixed(1)}%</span></span>
            <span>total pnl: <span className={totalPnl >= 0 ? "text-terminal-accent" : "text-terminal-red"}>
              {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(4)} USDT
            </span></span>
          </div>
        )}
      </div>
      <Card title={`Trades (${trades.length})`}>
        <TradeLog trades={trades} loading={loading} />
      </Card>
    </div>
  );
}
