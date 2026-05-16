"use client";
import { useTerminalState } from "@/hooks/useTerminalState";
import { WorkerGrid } from "@/components/watchdog/WorkerGrid";
import { Card } from "@/components/ui/Card";

export default function WatchdogPage() {
  const { watchdog } = useTerminalState();

  return (
    <div className="space-y-4">
      <h1 className="text-sm font-bold text-gray-200 uppercase tracking-widest">Watchdog</h1>
      <Card title="Worker Pool">
        <WorkerGrid watchdog={watchdog} />
      </Card>
    </div>
  );
}
