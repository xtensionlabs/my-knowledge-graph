"use client";

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

interface ConceptNodeData {
  title: string;
  kind: string;
  color: string;
  size: number;
  opacity: number;
  needs_review: boolean;
  dimmed: boolean;
  highlight: boolean;
}

function ConceptNodeImpl({ data }: NodeProps) {
  const d = data as unknown as ConceptNodeData;

  // Base opacity comes from freshness; hover/select state can override it down
  // (dim non-neighbors) or up (highlight focused + neighbors).
  const effectiveOpacity = d.dimmed ? 0.12 : d.highlight ? 1 : d.opacity;
  const scale = d.highlight ? 1.18 : 1;

  return (
    <div
      className="flex flex-col items-center transition-opacity duration-150"
      style={{ opacity: effectiveOpacity }}
    >
      <div
        className="rounded-full border transition-transform duration-150"
        style={{
          width: d.size,
          height: d.size,
          backgroundColor: d.color,
          borderColor: d.needs_review
            ? "var(--warn)"
            : d.highlight
            ? "var(--accent)"
            : "var(--bg)",
          borderWidth: d.needs_review || d.highlight ? 2 : 1.5,
          boxShadow: d.highlight
            ? "0 0 0 4px rgba(167, 139, 250, 0.25)"
            : d.needs_review
            ? "0 0 0 3px rgba(251, 191, 36, 0.15)"
            : "0 0 0 2px rgba(0,0,0,0.4)",
          transform: `scale(${scale})`,
        }}
      />
      <div
        className="mono text-[10px] sm:text-[11px] mt-1.5 max-w-[160px] text-center leading-tight truncate"
        style={{
          color:
            d.dimmed
              ? "var(--fg-dim)"
              : d.highlight || effectiveOpacity > 0.6
              ? "var(--fg)"
              : "var(--fg-muted)",
        }}
      >
        {d.title}
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!opacity-0 !pointer-events-none"
      />
      <Handle
        type="target"
        position={Position.Left}
        className="!opacity-0 !pointer-events-none"
      />
    </div>
  );
}

export const ConceptNode = memo(ConceptNodeImpl);
