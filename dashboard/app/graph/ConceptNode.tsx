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
}

function ConceptNodeImpl({ data }: NodeProps) {
  const d = data as unknown as ConceptNodeData;
  return (
    <div className="flex flex-col items-center" style={{ opacity: d.opacity }}>
      <div
        className="rounded-full border"
        style={{
          width: d.size,
          height: d.size,
          backgroundColor: d.color,
          borderColor: d.needs_review ? "var(--warn)" : "var(--bg)",
          borderWidth: d.needs_review ? 2 : 1.5,
          boxShadow: d.needs_review
            ? "0 0 0 3px rgba(251, 191, 36, 0.15)"
            : "0 0 0 2px rgba(0,0,0,0.4)",
        }}
      />
      <div
        className="mono text-[10px] text-fg-muted mt-1.5 max-w-[140px] text-center leading-tight truncate"
        style={{ color: d.opacity > 0.6 ? "var(--fg)" : "var(--fg-muted)" }}
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
