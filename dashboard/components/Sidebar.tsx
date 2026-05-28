"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Bot, GitGraph, LayoutDashboard, Network } from "lucide-react";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/graph", label: "Graph", icon: Network },
  { href: "/communities", label: "Communities", icon: GitGraph },
  { href: "/agents", label: "Agents", icon: Bot },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="w-60 shrink-0 border-r border-border bg-surface flex flex-col">
      <div className="px-5 py-6 border-b border-border">
        <Link href="/" className="flex items-center gap-2.5 group">
          <Activity className="w-5 h-5 text-accent transition-colors group-hover:text-fg" />
          <div className="flex flex-col leading-tight">
            <span className="mono text-sm font-semibold tracking-wide">
              SYNAPSE
            </span>
            <span className="mono text-[10px] text-fg-dim">cognitive os</span>
          </div>
        </Link>
      </div>

      <nav className="flex-1 py-4">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname?.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`relative flex items-center gap-3 px-5 py-2.5 text-sm transition-colors ${
                active
                  ? "text-fg bg-surface-2"
                  : "text-fg-muted hover:text-fg hover:bg-surface-2/50"
              }`}
            >
              {active && (
                <span className="absolute left-0 top-0 bottom-0 w-[2px] bg-accent" />
              )}
              <Icon className="w-4 h-4" />
              <span>{label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="px-5 py-4 border-t border-border">
        <div className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-[0.18em] text-fg-dim">
            host
          </span>
          <span className="mono text-xs text-fg-muted">localhost:8000</span>
        </div>
      </div>
    </aside>
  );
}
