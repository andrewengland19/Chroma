import type { CSSProperties, ReactNode } from "react";

export function Panel({ style, children, className = "", onDoubleClick }: {
  style?: CSSProperties; children?: ReactNode; className?: string; onDoubleClick?: React.MouseEventHandler;
}) {
  return (
    <section className={`absolute rounded-[8px] border border-line bg-panel ${className}`} style={style} onDoubleClick={onDoubleClick}>
      {children}
    </section>
  );
}

export function Segmented<T extends string>({
  label, options, value, onChange, width, testId,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T | undefined;
  onChange: (v: T) => void;
  width: number;
  testId?: string;
}) {
  return (
    <div style={{ width }}>
      <div className="label" style={{ marginBottom: 4 }}>{label}</div>
      <div role="radiogroup" aria-label={label} data-testid={testId}
        className="flex rounded-[6px] border border-ctl-line bg-ctl" style={{ height: 22, padding: 2 }}>
        {options.map((o) => {
          const on = o.value === value;
          return (
            <button key={o.value} role="radio" aria-checked={on} onClick={() => onChange(o.value)}
              className={`flex-1 rounded-[4.5px] font-bold ${on ? "bg-accent text-white" : "text-dim hover:text-fg"}`}
              style={{ fontSize: 9.5, letterSpacing: "0.02em" }}>
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function Chip({ color, children, width, testId }: { color: string; children: ReactNode; width: number; testId?: string }) {
  return (
    <div data-testid={testId} className="flex items-center rounded-full font-bold text-fg"
      style={{ width, height: 16, border: `1.2px solid ${color}`, fontSize: 8, paddingLeft: 6, gap: 4 }}>
      <span className="rounded-full" style={{ width: 5, height: 5, background: color }} />
      <span className="truncate">{children}</span>
    </div>
  );
}
