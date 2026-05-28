import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  hint?: string;
  className?: string;
  children: ReactNode;
}

export function Card({ title, hint, className = "", children }: CardProps) {
  return (
    <section
      className={`bg-surface border border-border p-5 ${className}`.trim()}
    >
      {(title || hint) && (
        <header className="mb-4 flex items-baseline justify-between gap-4">
          {title && (
            <h2 className="text-xs uppercase tracking-[0.15em] text-fg-muted">
              {title}
            </h2>
          )}
          {hint && <span className="mono text-xs text-fg-dim">{hint}</span>}
        </header>
      )}
      {children}
    </section>
  );
}
