import { GatewayError, getCommunities } from "@/lib/api";
import { Card } from "@/components/Card";
import { GatewayDownBanner } from "@/components/GatewayDownBanner";

export const dynamic = "force-dynamic";

export default async function CommunitiesPage() {
  try {
    const data = await getCommunities();
    return (
      <div className="px-8 py-8 max-w-[1400px]">
        <header className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">Communities</h1>
          <p className="text-sm text-fg-muted mt-1">
            Louvain clusters in your graph. Hub concepts are the highest-degree
            nodes within each cluster — the load-bearing ideas your thinking
            actually orbits.
          </p>
          <p className="mono text-xs text-fg-dim mt-2">
            {data.communities.length} communities · min size {data.min_size} ·
            top {data.hub_top_k} hubs each
          </p>
        </header>

        {data.communities.length === 0 ? (
          <Card title="No communities yet">
            <p className="text-sm text-fg-muted">
              Add more connected nodes and edges to your graph and the
              clustering algorithm will surface the structure here.
            </p>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.communities.map((c) => (
              <Card
                key={c.index}
                title={`Community ${c.index}`}
                hint={`${c.size} nodes`}
              >
                <ul className="space-y-2">
                  {c.hubs.map((h) => (
                    <li
                      key={h.node_id}
                      className="flex items-baseline justify-between gap-3"
                    >
                      <span className="text-fg truncate">{h.title}</span>
                      <span className="mono text-xs text-fg-dim shrink-0">
                        deg {h.degree}
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        )}
      </div>
    );
  } catch (err) {
    return (
      <div className="px-8 py-8 max-w-[1400px]">
        <h1 className="text-2xl font-semibold tracking-tight mb-6">
          Communities
        </h1>
        <GatewayDownBanner error={err} />
        {err instanceof GatewayError && err.status === 401 && (
          <p className="mt-4 text-xs text-fg-muted">
            401: check SYNAPSE_API_KEY.
          </p>
        )}
      </div>
    );
  }
}
