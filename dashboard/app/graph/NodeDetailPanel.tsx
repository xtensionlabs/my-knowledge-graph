"use client";

import {
  ArrowLeft,
  ArrowRight,
  Calendar,
  CircleAlert,
  ExternalLink,
  Loader2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { NodeDetailResponse } from "@/lib/types";
import { NodeBadge } from "@/components/NodeBadge";
import { relativeTime } from "@/lib/format";

interface Props {
  nodeId: string | null;
  onClose: () => void;
  onNavigate: (id: string) => void;
}

/**
 * Slides in from the right on desktop, slides up from the bottom on mobile.
 * Fetches /api/node/[id] when nodeId changes; clearing nodeId animates out.
 *
 * Keyboard: Escape closes. Backdrop click closes. The "Open detail page"
 * affordances are deliberately omitted — the graph IS the detail page.
 */
export function NodeDetailPanel({ nodeId, onClose, onNavigate }: Props) {
  const [data, setData] = useState<NodeDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch whenever the selected node changes.
  useEffect(() => {
    if (!nodeId) {
      // Keep the last data around briefly while closing animation plays.
      const t = setTimeout(() => setData(null), 200);
      return () => clearTimeout(t);
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/node/${encodeURIComponent(nodeId)}`)
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
        return body as NodeDetailResponse;
      })
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nodeId]);

  // Escape key closes.
  useEffect(() => {
    if (!nodeId) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [nodeId, onClose]);

  const open = nodeId !== null;

  return (
    <>
      {/* Backdrop — visible only on mobile so the panel feels modal there. */}
      <div
        aria-hidden
        onClick={onClose}
        className={`md:hidden fixed inset-0 z-40 bg-bg/70 transition-opacity ${
          open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        }`}
      />

      <aside
        className={`fixed z-50 bg-surface border-border flex flex-col transition-transform
          /* Mobile: bottom sheet, ~75vh */
          inset-x-0 bottom-0 h-[75vh] border-t rounded-t-xl
          /* Desktop: right rail, full height */
          md:inset-y-0 md:right-0 md:left-auto md:bottom-auto md:h-full md:w-[420px] md:border-t-0 md:border-l md:rounded-none
          ${open
            ? "translate-y-0 md:translate-x-0"
            : "translate-y-full md:translate-y-0 md:translate-x-full"
          }`}
      >
        {/* Mobile drag handle */}
        <div className="md:hidden flex justify-center pt-2 pb-1">
          <div className="w-10 h-1 rounded-full bg-border-strong" />
        </div>

        <header className="px-5 py-4 flex items-start justify-between gap-3 border-b border-border">
          <div className="min-w-0 flex-1">
            {data && (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <NodeBadge kind={data.node.type} />
                  {data.node.needs_review && (
                    <span className="mono text-[10px] uppercase tracking-wider text-warn flex items-center gap-1">
                      <CircleAlert className="w-3 h-3" />
                      needs review
                    </span>
                  )}
                </div>
                <h2 className="text-base md:text-lg font-semibold leading-tight break-words">
                  {data.node.title}
                </h2>
              </>
            )}
            {loading && !data && (
              <div className="flex items-center gap-2 text-sm text-fg-muted">
                <Loader2 className="w-4 h-4 animate-spin" />
                loading…
              </div>
            )}
            {error && (
              <p className="mono text-xs text-bad">✗ {error}</p>
            )}
          </div>
          <button
            type="button"
            aria-label="Close detail panel"
            onClick={onClose}
            className="p-1.5 -m-1 text-fg-muted hover:text-fg active:bg-surface-2 rounded transition-colors shrink-0"
          >
            <X className="w-5 h-5" />
          </button>
        </header>

        {data && (
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
            <MetadataSection data={data} />
            {data.node.content_excerpt && (
              <Section title="Content">
                <pre className="whitespace-pre-wrap break-words text-sm text-fg-muted leading-relaxed font-sans">
                  {data.node.content_excerpt}
                </pre>
              </Section>
            )}
            {data.node.tags.length > 0 && (
              <Section title="Tags">
                <div className="flex flex-wrap gap-1.5">
                  {data.node.tags
                    .filter((t) => !t.startsWith("_"))
                    .map((tag) => (
                      <span
                        key={tag}
                        className="mono text-[10px] px-1.5 py-0.5 border border-border text-fg-muted"
                      >
                        {tag}
                      </span>
                    ))}
                </div>
              </Section>
            )}
            <Section
              title={`Neighbors${
                data.neighbors.length ? ` (${data.neighbors.length})` : ""
              }`}
            >
              {data.neighbors.length === 0 ? (
                <p className="text-xs text-fg-muted italic">
                  No connections yet — this node is isolated.
                </p>
              ) : (
                <ul className="space-y-1">
                  {data.neighbors.map((n) => (
                    <li key={`${n.direction}-${n.node_id}`}>
                      <button
                        type="button"
                        onClick={() => onNavigate(n.node_id)}
                        className="w-full text-left flex items-center justify-between gap-2 px-2 py-2 hover:bg-surface-2 active:bg-surface-3 rounded transition-colors group"
                      >
                        <span className="flex items-center gap-2 min-w-0">
                          <DirectionIcon direction={n.direction} />
                          <NodeBadge kind={n.type} />
                          <span className="text-sm text-fg truncate">
                            {n.title}
                          </span>
                        </span>
                        <span className="mono text-[10px] text-fg-dim shrink-0 group-hover:text-fg-muted">
                          {n.relation} · {n.weight.toFixed(1)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <FooterStats data={data} />
          </div>
        )}
      </aside>
    </>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h3 className="text-[10px] uppercase tracking-[0.18em] text-fg-dim mb-2">
        {title}
      </h3>
      {children}
    </section>
  );
}

function DirectionIcon({ direction }: { direction: "in" | "out" }) {
  if (direction === "out")
    return <ArrowRight className="w-3 h-3 text-fg-dim shrink-0" />;
  return <ArrowLeft className="w-3 h-3 text-fg-dim shrink-0" />;
}

function MetadataSection({ data }: { data: NodeDetailResponse }) {
  const { metadata, node } = data;
  const hasTyped =
    metadata.sm2 || metadata.event_date || metadata.github;
  if (!hasTyped) return null;

  return (
    <Section title="Details">
      {metadata.sm2 && <Sm2Block sm2={metadata.sm2} />}
      {metadata.event_date && (
        <div className="flex items-center gap-2 text-sm">
          <Calendar className="w-4 h-4 text-warn" />
          <span className="text-fg">
            {new Date(metadata.event_date).toLocaleString(undefined, {
              dateStyle: "medium",
              timeStyle: "short",
            })}
          </span>
          <span className="mono text-xs text-fg-dim">
            ({relativeTime(metadata.event_date)})
          </span>
        </div>
      )}
      {metadata.github && (
        <div className="flex items-center gap-2 text-sm">
          <span className="mono text-[10px] text-fg-dim uppercase tracking-wider">
            github
          </span>
          {metadata.github.url ? (
            <a
              href={metadata.github.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline flex items-center gap-1 truncate"
            >
              {metadata.github.repo}
              <ExternalLink className="w-3 h-3 shrink-0" />
            </a>
          ) : (
            <span className="text-fg">{metadata.github.repo}</span>
          )}
        </div>
      )}
    </Section>
  );
}

function Sm2Block({ sm2 }: { sm2: NonNullable<NodeDetailResponse["metadata"]["sm2"]> }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span className="text-fg-muted">Next review</span>
        <span
          className={`mono text-xs ${sm2.overdue ? "text-warn" : "text-fg"}`}
        >
          {sm2.overdue ? "overdue · " : ""}
          {relativeTime(sm2.next_review)}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 mono text-[11px]">
        <SmallStat label="Interval" value={`${sm2.interval_days.toFixed(1)}d`} />
        <SmallStat label="Ease" value={sm2.ease_factor.toFixed(2)} />
        <SmallStat label="Reviews" value={String(sm2.review_count)} />
      </div>
    </div>
  );
}

function SmallStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface-2 border border-border px-2 py-1.5 flex flex-col">
      <span className="text-[9px] uppercase tracking-wider text-fg-dim">
        {label}
      </span>
      <span className="text-fg">{value}</span>
    </div>
  );
}

function FooterStats({ data }: { data: NodeDetailResponse }) {
  return (
    <footer className="pt-3 border-t border-border mono text-[10px] text-fg-dim flex flex-wrap gap-x-4 gap-y-1">
      <span>
        centrality {data.metadata.centrality.toFixed(2)}
      </span>
      <span>
        freshness {data.metadata.freshness.toFixed(2)}
      </span>
      {data.node.created_at && (
        <span>created {relativeTime(data.node.created_at)}</span>
      )}
      {data.node.updated_at && data.node.updated_at !== data.node.created_at && (
        <span>updated {relativeTime(data.node.updated_at)}</span>
      )}
    </footer>
  );
}

