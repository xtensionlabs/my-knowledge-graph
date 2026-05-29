"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Bot,
  GitGraph,
  LayoutDashboard,
  Menu,
  Network,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/graph", label: "Graph", icon: Network },
  { href: "/communities", label: "Communities", icon: GitGraph },
  { href: "/agents", label: "Agents", icon: Bot },
];

/**
 * Adaptive nav shell:
 *   - ≥ md: classic 240px left sidebar always visible
 *   - <  md: top header with hamburger that opens a full-height drawer
 *
 * One component owns NAV so labels and icons stay in sync across both modes.
 */
export function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  // Close drawer on route change.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Lock body scroll while drawer is open.
  useEffect(() => {
    if (open) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [open]);

  return (
    <>
      {/* Mobile header — hidden ≥ md */}
      <header className="md:hidden sticky top-0 z-30 flex items-center justify-between px-4 h-14 border-b border-border bg-surface/95 backdrop-blur supports-[backdrop-filter]:bg-surface/80">
        <Link href="/" className="flex items-center gap-2">
          <Activity className="w-4.5 h-4.5 text-accent" />
          <span className="mono text-sm font-semibold tracking-wide">SYNAPSE</span>
        </Link>
        <button
          type="button"
          aria-label="Open navigation"
          onClick={() => setOpen(true)}
          className="p-2 -mr-2 text-fg-muted hover:text-fg active:bg-surface-2 rounded transition-colors"
        >
          <Menu className="w-5 h-5" />
        </button>
      </header>

      {/* Mobile drawer — backdrop + panel */}
      <div
        className={`md:hidden fixed inset-0 z-40 transition-opacity ${
          open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        }`}
        aria-hidden={!open}
      >
        <div
          onClick={() => setOpen(false)}
          className="absolute inset-0 bg-bg/80 backdrop-blur-sm"
        />
        <aside
          className={`absolute left-0 top-0 bottom-0 w-72 max-w-[85vw] bg-surface border-r border-border flex flex-col transition-transform ${
            open ? "translate-x-0" : "-translate-x-full"
          }`}
        >
          <div className="px-5 py-4 border-b border-border flex items-center justify-between">
            <Link href="/" className="flex items-center gap-2.5 group">
              <Activity className="w-5 h-5 text-accent" />
              <div className="flex flex-col leading-tight">
                <span className="mono text-sm font-semibold tracking-wide">
                  SYNAPSE
                </span>
                <span className="mono text-[10px] text-fg-dim">cognitive os</span>
              </div>
            </Link>
            <button
              type="button"
              aria-label="Close navigation"
              onClick={() => setOpen(false)}
              className="p-2 -mr-2 text-fg-muted hover:text-fg"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
          <NavList pathname={pathname} />
          <HostFooter />
        </aside>
      </div>

      {/* Desktop sidebar — hidden < md */}
      <aside className="hidden md:flex w-60 shrink-0 border-r border-border bg-surface flex-col">
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
        <NavList pathname={pathname} />
        <HostFooter />
      </aside>
    </>
  );
}

function NavList({ pathname }: { pathname: string | null }) {
  return (
    <nav className="flex-1 py-4">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname?.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={`relative flex items-center gap-3 px-5 py-3 text-sm transition-colors min-h-[44px] ${
              active
                ? "text-fg bg-surface-2"
                : "text-fg-muted hover:text-fg hover:bg-surface-2/50 active:bg-surface-2"
            }`}
          >
            {active && (
              <span className="absolute left-0 top-0 bottom-0 w-[2px] bg-accent" />
            )}
            <Icon className="w-4 h-4 shrink-0" />
            <span>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

function HostFooter() {
  return (
    <div className="px-5 py-4 border-t border-border">
      <div className="flex flex-col gap-1">
        <span className="text-[10px] uppercase tracking-[0.18em] text-fg-dim">
          host
        </span>
        <span className="mono text-xs text-fg-muted truncate">
          synapse.xtensionlabs.com
        </span>
      </div>
    </div>
  );
}
