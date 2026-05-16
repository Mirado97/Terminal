"use client";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import type { LatencyStats } from "@/types";
import { fmtUs } from "@/lib/format";

interface Props {
  data: Record<string, LatencyStats>;
  title?: string;
}

export function LatencyChart({ data }: Props) {
  const chartData = Object.entries(data).map(([name, s]) => ({
    name,
    p50: s.p50,
    p95: s.p95,
    p99: s.p99,
  }));

  const hasData = chartData.some(d => d.p50 > 0 || d.p95 > 0 || d.p99 > 0);
  if (chartData.length === 0 || !hasData) {
    return (
      <div className="h-36 flex items-center justify-center text-terminal-muted text-xs">
        нет данных — данные появятся после первых замеров
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={144}>
      <BarChart data={chartData} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
        <XAxis dataKey="name" tick={{ fill: "#4a4a6a", fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: "#4a4a6a", fontSize: 10 }} axisLine={false} tickLine={false} width={60}
               tickFormatter={(v) => fmtUs(v)} />
        <Tooltip
          contentStyle={{ background: "#0d0d14", border: "1px solid #1a1a2e", fontSize: 11 }}
          formatter={(v: number, name: string) => [fmtUs(v), name.toUpperCase()]}
        />
        <Bar dataKey="p50" fill="#00ff88" opacity={0.4} radius={[2, 2, 0, 0]} isAnimationActive={false} />
        <Bar dataKey="p95" fill="#ffcc00" opacity={0.6} radius={[2, 2, 0, 0]} isAnimationActive={false} />
        <Bar dataKey="p99" fill="#ff4466" opacity={0.8} radius={[2, 2, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
