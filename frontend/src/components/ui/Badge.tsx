import { clsx } from "clsx";

type Variant = "green" | "red" | "yellow" | "gray" | "blue";

const VARIANTS: Record<Variant, string> = {
  green:  "bg-terminal-accent/10 text-terminal-accent border-terminal-accent/30",
  red:    "bg-terminal-red/10  text-terminal-red  border-terminal-red/30",
  yellow: "bg-terminal-yellow/10 text-terminal-yellow border-terminal-yellow/30",
  gray:   "bg-gray-800 text-gray-400 border-gray-700",
  blue:   "bg-blue-900/20 text-blue-400 border-blue-800/40",
};

interface BadgeProps {
  label: string;
  variant?: Variant;
  dot?: boolean;
}

export function Badge({ label, variant = "gray", dot }: BadgeProps) {
  return (
    <span className={clsx(
      "inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-xs font-medium",
      VARIANTS[variant],
    )}>
      {dot && <span className={clsx("w-1.5 h-1.5 rounded-full", {
        "bg-terminal-accent animate-pulse": variant === "green",
        "bg-terminal-red":    variant === "red",
        "bg-terminal-yellow": variant === "yellow",
        "bg-gray-400":        variant === "gray",
      })} />}
      {label}
    </span>
  );
}
