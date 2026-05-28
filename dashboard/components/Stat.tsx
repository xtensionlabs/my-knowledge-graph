interface StatProps {
  label: string;
  value: string | number;
  hint?: string;
  accent?: boolean;
}

export function Stat({ label, value, hint, accent = false }: StatProps) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-[0.18em] text-fg-dim">
        {label}
      </span>
      <span
        className={`mono text-3xl font-medium ${accent ? "text-accent" : "text-fg"}`}
      >
        {value}
      </span>
      {hint && <span className="mono text-xs text-fg-muted">{hint}</span>}
    </div>
  );
}
