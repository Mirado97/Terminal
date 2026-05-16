import { clsx } from "clsx";

type Status = "ok" | "warn" | "error" | "off";

const COLORS: Record<Status, string> = {
  ok:    "bg-terminal-accent shadow-[0_0_6px_rgba(0,255,136,0.6)]",
  warn:  "bg-terminal-yellow",
  error: "bg-terminal-red shadow-[0_0_6px_rgba(255,68,102,0.6)]",
  off:   "bg-gray-700",
};

export function StatusDot({ status, label }: { status: Status; label?: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className={clsx("w-2 h-2 rounded-full", COLORS[status])} />
      {label && <span className="text-xs text-gray-400">{label}</span>}
    </span>
  );
}
