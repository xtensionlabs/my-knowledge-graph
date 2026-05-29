"use client";

import {
  Background,
  Controls,
  ReactFlow,
  type Edge as RFEdge,
  type Node as RFNode,
  type NodeMouseHandler,
  type OnNodesChange,
  applyNodeChanges,
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
import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  CommunitiesResponse,
  GraphEdge,
  GraphNode,
  NodeKind,
} from "@/lib/types";
import { forceCluster } from "@/lib/force-cluster";
import { ConceptNode } from "./ConceptNode";
import { NodeDetailPanel } from "./NodeDetailPanel";

/**
 * Obsidian-style organic layout + click-to-inspect.
 *
 * Layout strategy:
 *   - d3-force precomputes positions ONCE (TICKS=400) so React Flow gets
 *     final coords, not zero-length edges on first paint.
 *   - A custom forceCluster pulls nodes in the same Louvain community
 *     toward their cluster centroid — gives the graph real structure
 *     instead of looking random.
 *   - Stronger Hebbian-weighted edges shorten the resting distance, so
 *     frequently co-activated concepts visibly clump.
 *
 * Interactivity:
 *   - Hovering a node highlights it + its 1-hop neighbors; everything
 *     else fades to 15% opacity.
 *   - Clicking a node opens NodeDetailPanel (right rail on desktop,
 *     bottom sheet on mobile) with content, neighbors, and type-specific
 *     metadata via the /api/node/[id] proxy.
 *   - Edge color tinted by relation type (applies_to / requires / etc.)
 *     so the graph communicates *why* nodes connect.
 */

const TICKS = 400;
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

const RELATION_COLORS: Record<string, string> = {
  applies_to: "var(--build)",
  requires: "var(--warn)",
  bridges: "var(--accent)",
  derived_from: "var(--info)",
  contradicts: "var(--bad)",
};
const DEFAULT_EDGE_COLOR = "var(--fg-dim)";

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  communities: CommunitiesResponse;
}

interface SimNode extends SimulationNodeDatum {
  id: string;
  cluster: number | null;
}

export function GraphCanvas({ nodes, edges, communities }: Props) {
  const initial = useMemo(
    () => computeLayout(nodes, edges, communities),
    [nodes, edges, communities],
  );

  // Local node state lets React Flow handle dragging without the parent
  // re-render reverting positions on every move.
  const [rfNodes, setRfNodes] = useState<RFNode[]>(initial.rfNodes);
  const [rfEdges, setRfEdges] = useState<RFEdge[]>(initial.rfEdges);
  useEffect(() => {
    setRfNodes(initial.rfNodes);
    setRfEdges(initial.rfEdges);
  }, [initial]);

  // Adjacency map drives the hover-highlight fade-out.
  const adjacency = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const e of edges) {
      if (!map.has(e.source)) map.set(e.source, new Set());
      if (!map.has(e.target)) map.set(e.target, new Set());
      map.get(e.source)!.add(e.target);
      map.get(e.target)!.add(e.source);
    }
    return map;
  }, [edges]);

  const [hovered, setHovered] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => setRfNodes((nds) => applyNodeChanges(changes, nds)),
    [],
  );
  const onNodeMouseEnter: NodeMouseHandler = useCallback(
    (_event, node) => setHovered(node.id),
    [],
  );
  const onNodeMouseLeave: NodeMouseHandler = useCallback(
    () => setHovered(null),
    [],
  );
  const onNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => setSelected(node.id),
    [],
  );

  // When something is focused (hovered or selected), dim non-neighbors.
  const focused = hovered ?? selected;
  const displayNodes = useMemo(() => {
    if (!focused) return rfNodes;
    const neighbors = adjacency.get(focused) ?? new Set();
    return rfNodes.map((n) => {
      const isFocus = n.id === focused;
      const isNeighbor = neighbors.has(n.id);
      return {
        ...n,
        data: {
          ...(n.data as Record<string, unknown>),
          dimmed: !isFocus && !isNeighbor,
          highlight: isFocus,
        },
      };
    });
  }, [rfNodes, focused, adjacency]);

  const displayEdges = useMemo(() => {
    if (!focused) return rfEdges;
    return rfEdges.map((e) => {
      const incident = e.source === focused || e.target === focused;
      const baseWidth = Number(e.style?.strokeWidth ?? 1);
      return {
        ...e,
        style: {
          ...(e.style ?? {}),
          opacity: incident ? 0.95 : 0.08,
          strokeWidth: incident ? Math.max(2, baseWidth * 1.5) : baseWidth,
        },
      };
    });
  }, [rfEdges, focused]);

  return (
    <>
      <ReactFlow
        nodes={displayNodes}
        edges={displayEdges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        onNodeClick={onNodeClick}
        onPaneClick={() => setSelected(null)}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.1}
        maxZoom={4}
        proOptions={{ hideAttribution: false }}
        defaultEdgeOptions={{ animated: false }}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        panOnDrag
        panOnScroll={false}
      >
        <Background gap={32} size={1} color="var(--border)" />
        <Controls position="bottom-right" showInteractive={false} />
      </ReactFlow>

      <NodeDetailPanel
        nodeId={selected}
        onClose={() => setSelected(null)}
        onNavigate={(id) => setSelected(id)}
      />
    </>
  );
}

function computeLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  communities: CommunitiesResponse,
) {
  // node → community index map (null for unclustered loners).
  const clusterByNode = new Map<string, number>();
  for (const c of communities.communities) {
    for (const id of c.node_ids) clusterByNode.set(id, c.index);
  }

  const simNodes: SimNode[] = nodes.map((n) => ({
    id: n.id,
    cluster: clusterByNode.get(n.id) ?? null,
  }));
  const simLinks: SimulationLinkDatum<SimNode>[] = edges.map((e) => ({
    source: e.source,
    target: e.target,
  }));

  // Build a fast (source,target) → weight lookup so the link.distance
  // callback doesn't do an O(E) scan per edge per tick.
  const weightByPair = new Map<string, number>();
  for (const e of edges) weightByPair.set(`${e.source}|${e.target}`, e.weight);

  forceSimulation(simNodes)
    .force(
      "link",
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks)
        .id((d) => d.id)
        .distance((l) => {
          const srcId = (l.source as SimNode).id;
          const tgtId = (l.target as SimNode).id;
          const w = weightByPair.get(`${srcId}|${tgtId}`) ?? 1;
          // Stronger edges → shorter resting distance.
          return 90 / Math.max(0.6, w);
        })
        .strength(0.6),
    )
    .force("charge", forceManyBody().strength(-340))
    .force("center", forceCenter(0, 0))
    .force("collide", forceCollide(38))
    .force(
      "cluster",
      forceCluster<SimNode>((n) => n.cluster, 0.12),
    )
    .stop()
    .tick(TICKS);

  const positions = new Map<string, { x: number; y: number }>();
  for (const s of simNodes) {
    positions.set(s.id, { x: s.x ?? 0, y: s.y ?? 0 });
  }

  const maxCentrality = nodes.reduce((m, n) => Math.max(m, n.centrality), 0) || 1;

  const rfNodes: RFNode[] = nodes.map((n) => {
    const size = 18 + Math.round((n.centrality / maxCentrality) * 28);
    return {
      id: n.id,
      type: "synapse",
      position: positions.get(n.id) ?? { x: 0, y: 0 },
      width: size,
      height: size + 22,
      data: {
        title: n.title,
        kind: n.type,
        color: COLORS[n.type] ?? "var(--fg-muted)",
        size,
        opacity: 0.4 + n.freshness * 0.6,
        needs_review: n.needs_review,
        dimmed: false,
        highlight: false,
      },
    };
  });

  const rfEdges: RFEdge[] = edges.map((e) => {
    const color = RELATION_COLORS[e.relation] ?? DEFAULT_EDGE_COLOR;
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      style: {
        stroke: color,
        strokeWidth: Math.max(1, Math.min(4, e.weight * 0.9)),
        opacity: 0.4 + Math.min(0.4, e.weight * 0.1),
      },
    };
  });

  return { rfNodes, rfEdges };
}
