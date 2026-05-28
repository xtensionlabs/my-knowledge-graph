import { AlertTriangle } from "lucide-react";

interface Props {
  error: unknown;
}

export function GatewayDownBanner({ error }: Props) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="bg-surface border border-bad/40 p-5 flex gap-3 items-start">
      <AlertTriangle className="w-5 h-5 text-bad shrink-0 mt-0.5" />
      <div className="min-w-0">
        <p className="text-sm text-bad font-medium">Gateway unreachable</p>
        <p className="mono text-xs text-fg-muted mt-1 break-all">{msg}</p>
        <p className="text-xs text-fg-muted mt-3">
          Start it with{" "}
          <code className="mono text-fg">uv run synapse start</code> and reload.
        </p>
      </div>
    </div>
  );
}
