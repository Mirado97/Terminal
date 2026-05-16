"use client";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import type { PnLPoint } from "@/hooks/useTerminalState";
import { fmtTime } from "@/lib/format";

interface Props {
  data: PnLPoint[];
}

export function PnLChart({ data }: Props) {
  if (data.length === 0) {
    return (
      <div className="h-36 flex items-center justify-center text-terminal-muted text-xs">
        нет данных
      </div>
    );
  }

  const isPositive = (data[data.length - 1]?.pnl ?? 0) >= 0;
  const color = isPositive ? "#00ff88" : "#ff4466";

  return (
    <ResponsiveContainer width="100%" height={144}>
      <AreaChart data={data} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor={color} stopOpacity={0.15} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="ts"
          tickFormatter={fmtTime}
          tick={{ fill: "#4a4a6a", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fill: "#4a4a6a", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          width={55}
          tickFormatter={(v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}`}
        />
        <Tooltip
          contentStyle={{ background: "#0d0d14", border: "1px solid #1a1a2e", fontSize: 11 }}
          labelFormatter={(v) => fmtTime(v as number)}
          formatter={(v: number) => [`${v > 0 ? "+" : ""}${v.toFixed(4)} USDT`, "PnL"]}
        />
        <ReferenceLine y={0} stroke="#1a1a2e" strokeDasharray="3 3" />
        <Area
          type="monotone"
          dataKey="pnl"
          stroke={color}
          strokeWidth={1.5}
          fill="url(#pnlGrad)"
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
