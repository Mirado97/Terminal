"use client";
import { useEffect, useState } from "react";
import { Card } from "@/components/ui/Card";
import { LatencyChart } from "@/components/charts/LatencyChart";
import { fmtUs, fmtMs } from "@/lib/format";
import type { LatencyStats } from "@/types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

interface LatencyData {
  execution: LatencyStats;
  [exchange: string]: LatencyStats | { ws: LatencyStats; rest: LatencyStats };
}

function StatsRow({ label, stats }: { label: string; stats: LatencyStats }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-terminal-border/40 last:border-0 text-xs">
      <span className="text-terminal-muted">{label}</span>
      <div className="flex gap-4">
        <span>P50: <span className="text-terminal-accent">{fmtUs(stats.p50)}</span></span>
        <span>P95: <span className="text-terminal-yellow">{fmtUs(stats.p95)}</span></span>
        <span>P99: <span className="text-terminal-red">{fmtUs(stats.p99)}</span></span>
        <span className="text-terminal-muted">n={stats.count}</span>
      </div>
    </div>
  );
}

export default function LatencyPage() {
  const [data, setData] = useState<LatencyData | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API}/api/latency`);
        setData(await res.json());
      } catch { /* ignore */ }
    }
    load();
    const id = setInterval(load, 2_000);
    return () => clearInterval(id);
  }, []);

  const exchanges = data
    ? Object.keys(data).filter(k => k !== "execution" && typeof (data[k] as any).ws !== "undefined")
    : [];

  const wsData: Record<string, LatencyStats> = {};
  const restData: Record<string, LatencyStats> = {};
  exchanges.forEach(ex => {
    const d = data![ex] as { ws: LatencyStats; rest: LatencyStats };
    wsData[ex] = d.ws;
    restData[ex] = d.rest;
  });

  return (
    <div className="space-y-4">
      <h1 className="text-sm font-bold text-gray-200 uppercase tracking-widest">Latency Monitor</h1>

      {/* Execution */}
      <Card title="Execution Latency">
        {data?.execution ? (
          <>
            <StatsRow label="execution_time_ms" stats={{ ...data.execution, p50: data.execution.p50, p95: data.execution.p95, p99: data.execution.p99 }} />
          </>
        ) : (
          <div className="text-terminal-muted text-xs">нет данных</div>
        )}
      </Card>

      {/* WebSocket */}
      <Card title="WebSocket Latency (µs)">
        {Object.values(wsData).some(s => s.count > 0) ? (
          <>
            {Object.entries(wsData).filter(([, s]) => s.count > 0).map(([ex, stats]) => (
              <StatsRow key={ex} label={ex} stats={stats} />
            ))}
            <div className="mt-3">
              <LatencyChart data={wsData} />
            </div>
          </>
        ) : (
          <div className="text-terminal-muted text-xs">нет данных — замеры появятся автоматически</div>
        )}
      </Card>

      {/* REST */}
      <Card title="REST API Latency (µs)">
        {Object.values(restData).some(s => s.count > 0) ? (
          Object.entries(restData).filter(([, s]) => s.count > 0).map(([ex, stats]) => (
            <StatsRow key={ex} label={ex} stats={stats} />
          ))
        ) : (
          <div className="text-terminal-muted text-xs">нет данных</div>
        )}
      </Card>
    </div>
  );
}
