"use client";
import { useEffect, useState } from "react";
import { SpreadTable } from "@/components/spreads/SpreadTable";
import { Card } from "@/components/ui/Card";
import type { SpreadRecord } from "@/types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export default function SpreadsPage() {
  const [spreads, setSpreads] = useState<SpreadRecord[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API}/api/spreads/live`);
        const data = await res.json();
        setSpreads(data.spreads ?? []);
      } catch { /* ignore */ }
      finally { setLoading(false); }
    }
    load();
    const id = setInterval(load, 5_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-sm font-bold text-gray-200 uppercase tracking-widest">Spread Scanner</h1>
      <Card title={`Opportunities (${spreads.length})`}>
        <SpreadTable spreads={spreads} loading={loading} />
      </Card>
    </div>
  );
}
