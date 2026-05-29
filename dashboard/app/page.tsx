import { CheckCircle2, XCircle } from "lucide-react";
import { GatewayError, getInbox, getOverview } from "@/lib/api";
import { Card } from "@/components/Card";
import { GatewayDownBanner } from "@/components/GatewayDownBanner";
import { InboxPanel } from "@/components/InboxPanel";
import { NodeBadge } from "@/components/NodeBadge";
import { Stat } from "@/components/Stat";
import { ms, relativeTime, usd } from "@/lib/format";
import type { RecentAgentRun } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  try {
    const [data, inbox] = await Promise.all([getOverview(), getInbox()]);
    return (
      <div className="px-4 py-5 md:px-8 md:py-8 max-w-[1400px] mx-auto">
        <PageHeader generatedAt={data.generated_at} />

        <div className="grid grid-cols-12 gap-3 md:gap-4 mb-4">
          <Card title="Graph" className="col-span-12 lg:col-span-6">
            <div className="grid grid-cols-3 gap-4 md:gap-6">
              <Stat label="Nodes" value={data.graph.nodes} accent />
              <Stat label="Edges" value={data.graph.edges} />
              <Stat
                label="Orphans"
                value={data.graph.orphans}
                hint={data.graph.orphans > 0 ? "no edges" : ""}
              />
            </div>
            <div className="mt-5 md:mt-6 flex flex-wrap gap-x-3 gap-y-2">
              {Object.entries(data.graph.nodes_by_type)
                .sort(([, a], [, b]) => b - a)
                .map(([kind, n]) => (
                  <div key={kind} className="flex items-center gap-2">
                    <NodeBadge kind={kind} />
                    <span className="mono text-xs text-fg-muted">{n}</span>
                  </div>
                ))}
            </div>
          </Card>

          <Card title="Capture" className="col-span-6 lg:col-span-3">
            <div className="grid grid-cols-2 gap-4 md:gap-6">
              <Stat label="Last 24h" value={data.capture.last_24h} accent />
              <Stat label="Total" value={data.capture.total} />
            </div>
          </Card>

          <Card title="Review queue" className="col-span-6 lg:col-span-3">
            <div className="grid grid-cols-2 gap-4 md:gap-6">
              <Stat
                label="Needs review"
                value={data.graph.needs_review}
                accent={data.graph.needs_review > 0}
              />
              <Stat
                label="Orphans"
                value={data.graph.orphans}
                accent={data.graph.orphans > 0}
              />
            </div>
          </Card>
        </div>

        <div className="mb-4">
          <InboxPanel total={inbox.total} items={inbox.items} />
        </div>

        <RecentRunsCard runs={data.recent_agent_runs} />
      </div>
    );
  } catch (err) {
    return (
      <div className="px-4 py-5 md:px-8 md:py-8 max-w-[1400px] mx-auto">
        <PageHeader generatedAt={null} />
        <GatewayDownBanner error={err} />
        {err instanceof GatewayError && err.status === 401 && (
          <p className="mt-4 text-xs text-fg-muted">
            Tip: 401 means SYNAPSE_API_KEY in{" "}
            <code className="mono">dashboard/.env.local</code> doesn&apos;t
            match SYNAPSE_BROWSER_API_KEY in the project root{" "}
            <code className="mono">.env</code>.
          </p>
        )}
      </div>
    );
  }
}

function PageHeader({ generatedAt }: { generatedAt: string | null }) {
  return (
    <header className="mb-6 md:mb-8 flex items-baseline justify-between gap-4">
      <div>
        <h1 className="text-xl md:text-2xl font-semibold tracking-tight">
          Overview
        </h1>
        <p className="text-xs md:text-sm text-fg-muted mt-1">
          The current shape of your graph.
        </p>
      </div>
      <span className="mono text-[10px] md:text-xs text-fg-dim shrink-0">
        {generatedAt ? `updated ${relativeTime(generatedAt)}` : "—"}
      </span>
    </header>
  );
}

function RecentRunsCard({ runs }: { runs: RecentAgentRun[] }) {
  return (
    <Card
      title="Recent agent runs"
      hint={`${runs.length} in last 24h`}
    >
      {runs.length === 0 ? (
        <p className="text-sm text-fg-muted">
          No agent runs in the last 24h.
        </p>
      ) : (
        <>
          {/* Desktop / tablet table */}
          <table className="hidden sm:table w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-[0.15em] text-fg-dim border-b border-border">
                <th className="py-2 font-normal">Agent</th>
                <th className="py-2 font-normal">Model</th>
                <th className="py-2 font-normal text-right">Cost</th>
                <th className="py-2 font-normal text-right">Latency</th>
                <th className="py-2 font-normal text-right">When</th>
                <th className="py-2 font-normal text-right pr-2">Status</th>
              </tr>
            </thead>
            <tbody className="mono">
              {runs.map((r, i) => (
                <tr key={i} className="border-b border-border/50 last:border-0">
                  <td className="py-2 text-fg">{r.agent}</td>
                  <td className="py-2 text-fg-muted text-xs">{r.model}</td>
                  <td className="py-2 text-right text-fg-muted">{usd(r.cost_usd)}</td>
                  <td className="py-2 text-right text-fg-muted">{ms(r.latency_ms)}</td>
                  <td className="py-2 text-right text-fg-muted">{relativeTime(r.at)}</td>
                  <td className="py-2 text-right pr-2">
                    {r.succeeded ? (
                      <CheckCircle2 className="w-4 h-4 text-ok inline" />
                    ) : (
                      <XCircle className="w-4 h-4 text-bad inline" />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Mobile card list */}
          <ul className="sm:hidden divide-y divide-border/60">
            {runs.map((r, i) => (
              <li key={i} className="py-3 flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-fg text-sm font-medium">{r.agent}</span>
                    {r.succeeded ? (
                      <CheckCircle2 className="w-3.5 h-3.5 text-ok shrink-0" />
                    ) : (
                      <XCircle className="w-3.5 h-3.5 text-bad shrink-0" />
                    )}
                  </div>
                  <p className="mono text-[11px] text-fg-dim truncate">
                    {r.model}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p className="mono text-xs text-fg-muted">{usd(r.cost_usd)}</p>
                  <p className="mono text-[11px] text-fg-dim">
                    {relativeTime(r.at)} · {ms(r.latency_ms)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
