import { GatewayError, getGraph } from "@/lib/api";
import { GatewayDownBanner } from "@/components/GatewayDownBanner";
import { GraphCanvas } from "./GraphCanvas";

export const dynamic = "force-dynamic";

export default async function GraphPage() {
  try {
    const data = await getGraph({ limit: 1000 });
    return (
      <div className="flex flex-col h-screen">
        <header className="px-8 py-6 border-b border-border flex items-baseline justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Graph</h1>
            <p className="text-sm text-fg-muted mt-1">
              {data.nodes.length} nodes · {data.edges.length} edges · drag to
              reorganize, scroll to zoom.
            </p>
          </div>
          <Legend />
        </header>
        <div className="flex-1 min-h-0">
          <GraphCanvas nodes={data.nodes} edges={data.edges} />
        </div>
      </div>
    );
  } catch (err) {
    return (
      <div className="px-8 py-8 max-w-[1400px]">
        <h1 className="text-2xl font-semibold tracking-tight mb-6">Graph</h1>
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
  { kind: "PERSON", color: "var(--fg-muted)" },
];

function Legend() {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1 mono text-[10px] uppercase tracking-wider">
      {LEGEND.map(({ kind, color }) => (
        <li key={kind} className="flex items-center gap-1.5 text-fg-muted">
          <span
            className="w-2 h-2 rounded-full"
            style={{ backgroundColor: color }}
          />
          {kind}
        </li>
      ))}
    </ul>
  );
}
