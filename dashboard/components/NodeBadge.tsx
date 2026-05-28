import type { NodeKind } from "@/lib/types";

const COLORS: Record<NodeKind, string> = {
  CONCEPT: "text-ok border-ok/40",
  FACT: "text-info border-info/40",
  BUILD: "text-build border-build/40",
  PERSON: "text-fg-muted border-fg-muted/40",
  EVENT: "text-warn border-warn/40",
  QUESTION: "text-accent border-accent/40",
  INSIGHT: "text-insight border-insight/40",
};

export function NodeBadge({ kind }: { kind: NodeKind | string }) {
  const cls = COLORS[kind as NodeKind] ?? "text-fg-muted border-fg-muted/40";
  return (
    <span
      className={`mono inline-flex items-center border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${cls}`}
    >
      {kind}
    </span>
  );
}
