import { CheckCircle2, MinusCircle, XCircle } from "lucide-react";
import { GatewayError, getAgents } from "@/lib/api";
import { Card } from "@/components/Card";
import { GatewayDownBanner } from "@/components/GatewayDownBanner";
import { Stat } from "@/components/Stat";
import { relativeTime, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function AgentsPage() {
  try {
    const data = await getAgents();
    const totalCost = data.agents.reduce((s, a) => s + a.cost_usd_7d, 0);
    const totalRuns = data.agents.reduce((s, a) => s + a.runs_7d, 0);

    return (
      <div className="px-8 py-8 max-w-[1400px]">
        <header className="mb-8 flex items-baseline justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
            <p className="text-sm text-fg-muted mt-1">
              Per-agent activity + cost over the last 7 days.
            </p>
          </div>
          <span className="mono text-xs text-fg-dim">
            updated {relativeTime(data.generated_at)}
          </span>
        </header>

        <div className="grid grid-cols-3 gap-4 mb-4">
          <Card>
            <Stat label="Agents tracked" value={data.agents.length} />
          </Card>
          <Card>
            <Stat label="Runs (7d)" value={totalRuns} accent />
          </Card>
          <Card>
            <Stat
              label="Spend (7d)"
              value={usd(totalCost)}
              accent={totalCost > 0}
              hint="across all agents"
            />
          </Card>
        </div>

        <Card title="By agent">
          {data.agents.length === 0 ? (
            <p className="text-sm text-fg-muted">
              No agent runs recorded yet. Trigger one with{" "}
              <code className="mono text-fg">uv run synapse brief</code> or{" "}
              <code className="mono text-fg">uv run synapse strategist run</code>.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-[0.15em] text-fg-dim border-b border-border">
                  <th className="py-2 font-normal">Agent</th>
                  <th className="py-2 font-normal">Model</th>
                  <th className="py-2 font-normal text-right">Runs (7d)</th>
                  <th className="py-2 font-normal text-right">Spend (7d)</th>
                  <th className="py-2 font-normal text-right">Latest run</th>
                  <th className="py-2 font-normal text-right pr-2">Status</th>
                </tr>
              </thead>
              <tbody className="mono">
                {[...data.agents]
                  .sort((a, b) => b.cost_usd_7d - a.cost_usd_7d)
                  .map((a) => (
                    <tr
                      key={a.agent}
                      className="border-b border-border/50 last:border-0"
                    >
                      <td className="py-2 text-fg">{a.agent}</td>
                      <td className="py-2 text-fg-muted text-xs">
                        {a.latest_model ?? "—"}
                      </td>
                      <td className="py-2 text-right text-fg-muted">
                        {a.runs_7d}
                      </td>
                      <td className="py-2 text-right text-fg-muted">
                        {usd(a.cost_usd_7d)}
                      </td>
                      <td className="py-2 text-right text-fg-muted">
                        {relativeTime(a.latest_run_at)}
                      </td>
                      <td className="py-2 text-right pr-2">
                        <StatusIcon ok={a.latest_succeeded} />
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    );
  } catch (err) {
    return (
      <div className="px-8 py-8 max-w-[1400px]">
        <h1 className="text-2xl font-semibold tracking-tight mb-6">Agents</h1>
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

function StatusIcon({ ok }: { ok: boolean | null }) {
  if (ok === null)
    return <MinusCircle className="w-4 h-4 text-fg-dim inline" />;
  if (ok) return <CheckCircle2 className="w-4 h-4 text-ok inline" />;
  return <XCircle className="w-4 h-4 text-bad inline" />;
}
