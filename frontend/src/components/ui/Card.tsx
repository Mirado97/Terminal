import { clsx } from "clsx";

interface CardProps {
  title?: string;
  children: React.ReactNode;
  className?: string;
  accent?: boolean;
}

export function Card({ title, children, className, accent }: CardProps) {
  return (
    <div className={clsx(
      "bg-terminal-panel border rounded-lg p-4",
      accent ? "border-terminal-accent/30" : "border-terminal-border",
      className,
    )}>
      {title && (
        <div className="text-xs text-terminal-muted uppercase tracking-widest mb-3 font-semibold">
          {title}
        </div>
      )}
      {children}
    </div>
  );
}
