"use client";

import {
  Background,
  Controls,
  ReactFlow,
  type Edge as RFEdge,
  type Node as RFNode,
} from "@xyflow/react";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { useMemo } from "react";
import type { GraphEdge, GraphNode, NodeKind } from "@/lib/types";
import { ConceptNode } from "./ConceptNode";

/**
 * Renders the graph with an Obsidian-style organic layout.
 *
 * Strategy: run d3-force-simulation synchronously for a fixed number of ticks
 * to compute (x, y) for every node, then hand the result to React Flow which
 * provides pan/zoom/drag interactivity from there.
 *
 * The simulation only runs ONCE per render (memoized on the node/edge inputs),
 * not every frame — React Flow takes over once the layout settles.
 */

const TICKS = 300;
const NODE_TYPES = { synapse: ConceptNode } as const;

const COLORS: Record<NodeKind, string> = {
  CONCEPT: "var(--ok)",
  FACT: "var(--info)",
  BUILD: "var(--build)",
  PERSON: "var(--fg-muted)",
  EVENT: "var(--warn)",
  QUESTION: "var(--accent)",
  INSIGHT: "var(--insight)",
};

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

interface SimNode extends SimulationNodeDatum {
  id: string;
}

export function GraphCanvas({ nodes, edges }: Props) {
  const { rfNodes, rfEdges } = useMemo(
    () => computeLayout(nodes, edges),
    [nodes, edges],
  );

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={NODE_TYPES}
      fitView
      fitViewOptions={{ padding: 0.2 }}
      minZoom={0.1}
      maxZoom={4}
      proOptions={{ hideAttribution: false }}
      defaultEdgeOptions={{ animated: false }}
      nodesDraggable
      nodesConnectable={false}
      elementsSelectable
    >
      <Background gap={32} size={1} color="var(--border)" />
      <Controls position="bottom-right" showInteractive={false} />
    </ReactFlow>
  );
}

function computeLayout(nodes: GraphNode[], edges: GraphEdge[]) {
  // The simulation works on its own node objects (must be SimulationNodeDatum).
  const simNodes: SimNode[] = nodes.map((n) => ({ id: n.id }));
  const simLinks: SimulationLinkDatum<SimNode>[] = edges.map((e) => ({
    source: e.source,
    target: e.target,
  }));

  forceSimulation(simNodes)
    .force(
      "link",
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks)
        .id((d) => d.id)
        .distance((l) => {
          const edge = edges.find(
            (e) =>
              e.source === (l.source as SimNode).id &&
              e.target === (l.target as SimNode).id,
          );
          // Higher Hebbian weight → shorter resting distance.
          const w = edge?.weight ?? 1;
          return 120 / Math.max(0.5, w);
        })
        .strength(0.4),
    )
    .force("charge", forceManyBody().strength(-280))
    .force("center", forceCenter(0, 0))
    .force("collide", forceCollide(36))
    .stop()
    .tick(TICKS);

  const positions = new Map<string, { x: number; y: number }>();
  for (const s of simNodes) {
    positions.set(s.id, { x: s.x ?? 0, y: s.y ?? 0 });
  }

  // Find max centrality to scale node size proportionally.
  const maxC = nodes.reduce((m, n) => Math.max(m, n.centrality), 0) || 1;

  const rfNodes: RFNode[] = nodes.map((n) => {
    const size = 18 + Math.round((n.centrality / maxC) * 26);
    return {
      id: n.id,
      type: "synapse",
      position: positions.get(n.id) ?? { x: 0, y: 0 },
      // Explicit dimensions so React Flow can compute edge endpoints up-front,
      // instead of waiting for DOM measurement (which leaves edges collapsed
      // to zero length on first paint).
      width: size,
      height: size + 18, // circle + label below
      data: {
        title: n.title,
        kind: n.type,
        color: COLORS[n.type] ?? "var(--fg-muted)",
        size,
        opacity: 0.35 + n.freshness * 0.65,
        needs_review: n.needs_review,
      },
    };
  });

  const rfEdges: RFEdge[] = edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    style: {
      stroke: "var(--fg-dim)",
      strokeWidth: Math.max(1, Math.min(4, e.weight)),
      opacity: 0.55 + Math.min(0.35, e.weight * 0.1),
    },
  }));

  return { rfNodes, rfEdges };
}
