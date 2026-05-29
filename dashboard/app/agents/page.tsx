import { CheckCircle2, MinusCircle, XCircle } from "lucide-react";
import { GatewayError, getAgents } from "@/lib/api";
import { Card } from "@/components/Card";
import { GatewayDownBanner } from "@/components/GatewayDownBanner";
import { Stat } from "@/components/Stat";
import { relativeTime, usd } from "@/lib/format";
import type { AgentRollup } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AgentsPage() {
  try {
    const data = await getAgents();
    const totalCost = data.agents.reduce((s, a) => s + a.cost_usd_7d, 0);
    const totalRuns = data.agents.reduce((s, a) => s + a.runs_7d, 0);
    const sorted = [...data.agents].sort((a, b) => b.cost_usd_7d - a.cost_usd_7d);

    return (
      <div className="px-4 py-5 md:px-8 md:py-8 max-w-[1400px] mx-auto">
        <header className="mb-6 md:mb-8 flex items-baseline justify-between gap-4">
          <div>
            <h1 className="text-xl md:text-2xl font-semibold tracking-tight">
              Agents
            </h1>
            <p className="text-xs md:text-sm text-fg-muted mt-1">
              Per-agent activity + cost over the last 7 days.
            </p>
          </div>
          <span className="mono text-[10px] md:text-xs text-fg-dim shrink-0">
            updated {relativeTime(data.generated_at)}
          </span>
        </header>

        <div className="grid grid-cols-3 gap-3 md:gap-4 mb-4">
          <Card>
            <Stat label="Agents" value={data.agents.length} />
          </Card>
          <Card>
            <Stat label="Runs (7d)" value={totalRuns} accent />
          </Card>
          <Card>
            <Stat
              label="Spend (7d)"
              value={usd(totalCost)}
              accent={totalCost > 0}
            />
          </Card>
        </div>

        <Card title="By agent">
          {sorted.length === 0 ? (
            <p className="text-sm text-fg-muted">
              No agent runs recorded yet. Trigger one with{" "}
              <code className="mono text-fg">uv run synapse brief</code> or{" "}
              <code className="mono text-fg">uv run synapse strategist run</code>.
            </p>
          ) : (
            <>
              {/* Desktop table */}
              <table className="hidden sm:table w-full text-sm">
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
                  {sorted.map((a) => (
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

              {/* Mobile card list */}
              <ul className="sm:hidden divide-y divide-border/60">
                {sorted.map((a) => (
                  <AgentRowMobile key={a.agent} a={a} />
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>
    );
  } catch (err) {
    return (
      <div className="px-4 py-5 md:px-8 md:py-8 max-w-[1400px] mx-auto">
        <h1 className="text-xl md:text-2xl font-semibold tracking-tight mb-6">
          Agents
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

function AgentRowMobile({ a }: { a: AgentRollup }) {
  return (
    <li className="py-3 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-fg text-sm font-medium">{a.agent}</span>
          <StatusIcon ok={a.latest_succeeded} />
        </div>
        <p className="mono text-[11px] text-fg-dim truncate mt-0.5">
          {a.latest_model ?? "—"}
        </p>
      </div>
      <div className="text-right shrink-0">
        <p className="mono text-xs text-fg-muted">
          {usd(a.cost_usd_7d)} <span className="text-fg-dim">· {a.runs_7d} runs</span>
        </p>
        <p className="mono text-[11px] text-fg-dim">
          {relativeTime(a.latest_run_at)}
        </p>
      </div>
    </li>
  );
}

function StatusIcon({ ok }: { ok: boolean | null }) {
  if (ok === null)
    return <MinusCircle className="w-4 h-4 text-fg-dim inline shrink-0" />;
  if (ok) return <CheckCircle2 className="w-4 h-4 text-ok inline shrink-0" />;
  return <XCircle className="w-4 h-4 text-bad inline shrink-0" />;
}
