"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";

const NAV = [
  { href: "/dashboard", label: "Dashboard",  icon: "▣" },
  { href: "/spreads",   label: "Spreads",    icon: "⇄" },
  { href: "/watchdog",  label: "Watchdog",   icon: "◎" },
  { href: "/trades",    label: "Trades",     icon: "◈" },
  { href: "/latency",   label: "Latency",    icon: "⧗" },
  { href: "/risk",      label: "Risk",       icon: "⚠" },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="w-48 bg-terminal-panel border-r border-terminal-border flex flex-col shrink-0">
      {/* Logo */}
      <div className="px-4 py-5 border-b border-terminal-border">
        <div className="text-terminal-accent font-bold text-sm tracking-widest">ARB</div>
        <div className="text-terminal-muted text-xs mt-0.5">TERMINAL v0.1</div>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-4 space-y-0.5 px-2">
        {NAV.map(({ href, label, icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2 rounded text-xs transition-colors",
                active
                  ? "bg-terminal-accent/10 text-terminal-accent border border-terminal-accent/20"
                  : "text-gray-500 hover:text-gray-200 hover:bg-white/5",
              )}
            >
              <span className="text-base w-4 text-center">{icon}</span>
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-terminal-border text-xs text-terminal-muted">
        Python 3.10 • asyncio
      </div>
    </aside>
  );
}
