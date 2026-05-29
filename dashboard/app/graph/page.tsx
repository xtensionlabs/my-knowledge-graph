import { GatewayError, getCommunities, getGraph } from "@/lib/api";
import { GatewayDownBanner } from "@/components/GatewayDownBanner";
import { GraphCanvas } from "./GraphCanvas";

export const dynamic = "force-dynamic";

export default async function GraphPage() {
  try {
    // Fetch graph + communities in parallel — communities feed the
    // cluster-aware d3-force layout that makes the graph readable.
    const [graph, communities] = await Promise.all([
      getGraph({ limit: 1000 }),
      getCommunities(),
    ]);

    return (
      <div className="flex flex-col h-[calc(100vh-3.5rem)] md:h-screen">
        <header className="px-4 py-3 md:px-8 md:py-5 border-b border-border flex flex-col md:flex-row md:items-baseline md:justify-between gap-2">
          <div>
            <h1 className="text-xl md:text-2xl font-semibold tracking-tight">
              Graph
            </h1>
            <p className="text-xs md:text-sm text-fg-muted mt-1">
              {graph.nodes.length} nodes · {graph.edges.length} edges ·{" "}
              {communities.communities.length} communities · tap a node
            </p>
          </div>
          <Legend />
        </header>
        <div className="flex-1 min-h-0">
          <GraphCanvas
            nodes={graph.nodes}
            edges={graph.edges}
            communities={communities}
          />
        </div>
      </div>
    );
  } catch (err) {
    return (
      <div className="px-4 py-5 md:px-8 md:py-8 max-w-[1400px] mx-auto">
        <h1 className="text-xl md:text-2xl font-semibold tracking-tight mb-6">
          Graph
        </h1>
        <GatewayDownBanner error={err} />
        {err instanceof GatewayError && err.status === 401 && (
          <p className="mt-4 text-xs text-fg-muted">
            401: check SYNAPSE_API_KEY in dashboard/.env.local.
          </p>
        )}
      </div>
    );
  }
}

const LEGEND: Array<{ kind: string; color: string }> = [
  { kind: "CONCEPT", color: "var(--ok)" },
  { kind: "FACT", color: "var(--info)" },
  { kind: "BUILD", color: "var(--build)" },
  { kind: "EVENT", color: "var(--warn)" },
  { kind: "QUESTION", color: "var(--accent)" },
  { kind: "INSIGHT", color: "var(--insight)" },
];

function Legend() {
  return (
    <ul className="flex flex-wrap gap-x-2.5 gap-y-1 mono text-[9px] sm:text-[10px] uppercase tracking-wider">
      {LEGEND.map(({ kind, color }) => (
        <li key={kind} className="flex items-center gap-1 text-fg-muted">
          <span
            className="w-1.5 h-1.5 sm:w-2 sm:h-2 rounded-full"
            style={{ backgroundColor: color }}
          />
          {kind}
        </li>
      ))}
    </ul>
  );
}
