/**
 * Custom d3-force that pulls nodes belonging to the same community toward
 * their cluster's centroid. Modelled on d3's own `forceX`/`forceY` source.
 *
 * The standard d3-force layout already handles repulsion (charge), edges
 * (link), and overlap (collide). What it doesn't do is preserve the *graph
 * structure* — clusters that should sit together (concepts in the same
 * Louvain community) end up scattered, and the layout looks "random."
 *
 * This force fixes that without overriding d3's other forces — at each
 * tick it nudges every node toward the centroid of its assigned cluster.
 * Strength is tunable; too high and clusters collapse into single points,
 * too low and clustering vanishes. Default 0.08 gives Obsidian-style
 * organic clumps that still let inter-cluster edges pull things together.
 */

import type { SimulationNodeDatum } from "d3-force";

type NodeWithCluster = SimulationNodeDatum & { cluster?: string | number | null };

interface Centroid {
  x: number;
  y: number;
  count: number;
}

export interface ForceCluster<N extends NodeWithCluster> {
  (alpha: number): void;
  initialize(nodes: N[], random: () => number): void;
  strength(): number;
  strength(value: number): ForceCluster<N>;
}

export function forceCluster<N extends NodeWithCluster>(
  getCluster: (node: N) => string | number | null | undefined,
  initialStrength = 0.08,
): ForceCluster<N> {
  let strength = initialStrength;
  let nodes: N[] = [];

  const force = ((alpha: number) => {
    if (nodes.length === 0) return;

    // Recompute centroids each tick — they move with the layout.
    const centroids = new Map<string | number, Centroid>();
    for (const node of nodes) {
      const cluster = getCluster(node);
      if (cluster === null || cluster === undefined) continue;
      let c = centroids.get(cluster);
      if (!c) {
        c = { x: 0, y: 0, count: 0 };
        centroids.set(cluster, c);
      }
      c.x += node.x ?? 0;
      c.y += node.y ?? 0;
      c.count += 1;
    }
    for (const c of centroids.values()) {
      if (c.count > 0) {
        c.x /= c.count;
        c.y /= c.count;
      }
    }

    // Pull each node toward its centroid.
    for (const node of nodes) {
      const cluster = getCluster(node);
      if (cluster === null || cluster === undefined) continue;
      const centroid = centroids.get(cluster);
      if (!centroid || centroid.count <= 1) continue;
      node.vx = (node.vx ?? 0) + (centroid.x - (node.x ?? 0)) * strength * alpha;
      node.vy = (node.vy ?? 0) + (centroid.y - (node.y ?? 0)) * strength * alpha;
    }
  }) as ForceCluster<N>;

  force.initialize = (_nodes: N[]) => {
    nodes = _nodes;
  };

  // Overload signature for the d3-force convention: `.strength()` reads,
  // `.strength(x)` writes and returns the force for chaining.
  function strengthAccessor(): number;
  function strengthAccessor(value: number): ForceCluster<N>;
  function strengthAccessor(value?: number): number | ForceCluster<N> {
    if (value === undefined) return strength;
    strength = value;
    return force;
  }
  force.strength = strengthAccessor as ForceCluster<N>["strength"];

  return force;
}
