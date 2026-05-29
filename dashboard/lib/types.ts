/**
 * Typed response shapes from the Synapse gateway's /dashboard/* endpoints.
 * Mirrors `synapse/gateway/routes/dashboard.py` — keep in sync when the
 * backend response changes.
 */

export type NodeKind =
  | "CONCEPT"
  | "FACT"
  | "BUILD"
  | "PERSON"
  | "EVENT"
  | "QUESTION"
  | "INSIGHT";

export interface RecentAgentRun {
  agent: string;
  model: string;
  succeeded: boolean;
  cost_usd: number;
  latency_ms: number;
  at: string | null;
}

export interface OverviewResponse {
  generated_at: string;
  graph: {
    nodes: number;
    edges: number;
    nodes_by_type: Record<string, number>;
    orphans: number;
    needs_review: number;
  };
  capture: {
    total: number;
    last_24h: number;
  };
  recent_agent_runs: RecentAgentRun[];
}

export interface GraphNode {
  id: string;
  type: NodeKind;
  title: string;
  centrality: number;
  freshness: number;
  needs_review: boolean;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  weight: number;
  created_by: string;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Hub {
  node_id: string;
  title: string;
  degree: number;
}

export interface Community {
  index: number;
  size: number;
  node_ids: string[];
  hubs: Hub[];
}

export interface CommunitiesResponse {
  min_size: number;
  hub_top_k: number;
  communities: Community[];
}

export interface AgentRollup {
  agent: string;
  latest_run_at: string | null;
  latest_succeeded: boolean | null;
  latest_model: string | null;
  runs_7d: number;
  cost_usd_7d: number;
}

export interface AgentsResponse {
  generated_at: string;
  agents: AgentRollup[];
}

export interface InboxItem {
  filename: string;
  source: string;
  size_bytes: number;
  created_at: string;
}

export interface InboxResponse {
  total: number;
  items: InboxItem[];
}

export interface LibrarianRunResponse {
  ok: boolean;
  summary: string;
  artifacts: Record<string, unknown>;
  errors: string[];
}

export interface NeighborRef {
  node_id: string;
  title: string;
  type: NodeKind;
  direction: "in" | "out";
  relation: string;
  weight: number;
}

export interface SM2Metadata {
  next_review: string;
  last_reviewed: string | null;
  interval_days: number;
  ease_factor: number;
  review_count: number;
  overdue: boolean;
}

export interface GithubLinkMetadata {
  repo: string;
  url?: string;
}

export interface NodeDetailResponse {
  node: {
    id: string;
    type: NodeKind;
    title: string;
    content_excerpt: string;
    tags: string[];
    needs_review: boolean;
    created_at: string | null;
    updated_at: string | null;
  };
  neighbors: NeighborRef[];
  metadata: {
    centrality: number;
    freshness: number;
    sm2?: SM2Metadata;
    event_date?: string;
    github?: GithubLinkMetadata;
  };
}
