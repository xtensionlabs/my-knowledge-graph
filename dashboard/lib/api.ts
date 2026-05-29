/**
 * Server-side fetch wrapper for the Synapse gateway.
 *
 * This module is server-only — it reads SYNAPSE_API_KEY from the Node process
 * env so the key is never bundled into client JS. All page components call
 * these helpers from Server Components.
 *
 * Per the M5b auth model: the gateway requires the x-synapse-api-key header
 * (reusing SYNAPSE_BROWSER_API_KEY on the backend).
 */

import "server-only";
import type {
  AgentsResponse,
  CommunitiesResponse,
  GraphResponse,
  InboxResponse,
  LibrarianRunResponse,
  NodeDetailResponse,
  NodeKind,
  OverviewResponse,
} from "./types";

const GATEWAY_URL = process.env.SYNAPSE_GATEWAY_URL ?? "http://127.0.0.1:8000";
const API_KEY = process.env.SYNAPSE_API_KEY ?? "";

class GatewayError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "GatewayError";
  }
}

async function fetchSynapse<T>(
  path: string,
  init: { method?: "GET" | "POST" } = {},
): Promise<T> {
  const url = `${GATEWAY_URL}${path}`;
  const headers: Record<string, string> = { accept: "application/json" };
  if (API_KEY) headers["x-synapse-api-key"] = API_KEY;

  // Default fetch is uncached in Next 16 — we want fresh data on every nav.
  const res = await fetch(url, {
    method: init.method ?? "GET",
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new GatewayError(res.status, `${res.status} ${path}: ${body.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export async function getOverview(): Promise<OverviewResponse> {
  return fetchSynapse<OverviewResponse>("/dashboard/overview");
}

export async function getGraph(opts: {
  types?: NodeKind[];
  limit?: number;
} = {}): Promise<GraphResponse> {
  const params = new URLSearchParams();
  if (opts.types?.length) params.set("types", opts.types.join(","));
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return fetchSynapse<GraphResponse>(`/dashboard/graph${qs ? `?${qs}` : ""}`);
}

export async function getCommunities(): Promise<CommunitiesResponse> {
  return fetchSynapse<CommunitiesResponse>("/dashboard/communities");
}

export async function getAgents(): Promise<AgentsResponse> {
  return fetchSynapse<AgentsResponse>("/dashboard/agents");
}

export async function getInbox(): Promise<InboxResponse> {
  return fetchSynapse<InboxResponse>("/dashboard/inbox");
}

export async function triggerLibrarian(): Promise<LibrarianRunResponse> {
  return fetchSynapse<LibrarianRunResponse>("/dashboard/librarian/run", {
    method: "POST",
  });
}

export async function getNodeDetail(nodeId: string): Promise<NodeDetailResponse> {
  return fetchSynapse<NodeDetailResponse>(`/dashboard/node/${encodeURIComponent(nodeId)}`);
}

export { GatewayError };
