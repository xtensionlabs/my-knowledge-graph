"use client";

import { Inbox, Loader2, Play } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import type { InboxItem } from "@/lib/types";
import { Card } from "@/components/Card";
import { NodeBadge } from "@/components/NodeBadge";
import { relativeTime } from "@/lib/format";

interface Props {
  total: number;
  items: InboxItem[];
}

const SOURCE_LABELS: Record<string, string> = {
  manual: "manual",
  telegram: "telegram",
  browser: "browser",
  email: "email",
  clipboard: "clipboard",
  voice: "voice",
  ocr: "ocr",
  git: "git",
};

export function InboxPanel({ total, items }: Props) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runLibrarian = async () => {
    setError(null);
    setStatus("processing…");
    try {
      const res = await fetch("/api/librarian/run", { method: "POST" });
      const body = await res.json();
      if (!res.ok || !body.ok) {
        // Prefer the first agent-reported error (which carries the real cause —
        // 400 from Anthropic, 401 invalid token, etc.) over the generic summary.
        const detail =
          (Array.isArray(body.errors) && body.errors[0]) ||
          body.summary ||
          `HTTP ${res.status}`;
        throw new Error(detail);
      }
      setStatus(body.summary);
      startTransition(() => router.refresh());
    } catch (err) {
      setStatus(null);
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <Card
      title="Inbox"
      hint={total === 0 ? "empty" : `${total} pending`}
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2 text-sm text-fg-muted">
          <Inbox className="w-4 h-4 text-fg-dim" />
          <span>
            {total === 0
              ? "Nothing waiting — Librarian has it all."
              : `${total} capture${total === 1 ? "" : "s"} awaiting the Librarian.`}
          </span>
        </div>
        <button
          type="button"
          onClick={runLibrarian}
          disabled={pending || total === 0}
          className="inline-flex items-center gap-1.5 mono text-xs uppercase tracking-wider px-3 py-1.5 border border-border bg-surface-2 text-fg hover:bg-surface-3 hover:border-fg-dim transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {pending ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Play className="w-3.5 h-3.5" />
          )}
          Run Librarian
        </button>
      </div>

      {items.length > 0 && (
        <ul className="divide-y divide-border/60 border-t border-border/60">
          {items.slice(0, 8).map((item) => (
            <li
              key={item.filename}
              className="flex items-center justify-between gap-3 py-2"
            >
              <div className="flex items-center gap-3 min-w-0">
                <NodeBadge kind={SOURCE_LABELS[item.source] || item.source} />
                <span className="mono text-xs text-fg-muted truncate">
                  {item.filename}
                </span>
              </div>
              <span className="mono text-xs text-fg-dim shrink-0">
                {relativeTime(item.created_at)} · {item.size_bytes}b
              </span>
            </li>
          ))}
          {total > items.length && (
            <li className="py-2 mono text-xs text-fg-dim">
              … and {total - items.length} more
            </li>
          )}
        </ul>
      )}

      {status && !error && (
        <p className="mono text-xs text-ok mt-3">{status}</p>
      )}
      {error && <p className="mono text-xs text-bad mt-3">✗ {error}</p>}
    </Card>
  );
}
