// A thin horizontal slider styled like gui-3 (accent fill, white knob). Commits on release
// (pointer up / key up), so each drag is one groups.upsert.
import { useRef, useState } from "react";

export function Slider({ label, value, steps, format, onCommit, testId, maxIndex, capTitle }: {
  label: string;
  value: number;           // index into steps
  steps: number;           // number of positions drawn (0 .. steps-1)
  format: (i: number) => string;
  onCommit: (i: number) => void;
  testId?: string;
  maxIndex?: number;       // hard stop: positions above it are drawn but unreachable (safety caps)
  capTitle?: string;
}) {
  const top = maxIndex ?? steps - 1;
  const [drag, setDrag] = useState<number | null>(null);
  const track = useRef<HTMLDivElement>(null);
  const i = drag ?? value;
  const frac = steps > 1 ? i / (steps - 1) : 0;
  const at = (clientX: number) => {
    const r = track.current!.getBoundingClientRect();
    const f = r.width ? (clientX - r.left) / r.width : 0;
    return Math.min(top, Math.round(Math.min(1, Math.max(0, f)) * (steps - 1)));
  };
  const commit = (n: number) => {
    setDrag(null);
    if (n !== value) onCommit(n);
  };
  return (
    <div className="relative" style={{ height: 20 }}>
      <span className="absolute text-fg" style={{ left: 0, top: 3, fontSize: 10 }}>{label}</span>
      <div ref={track} role="slider" tabIndex={0} aria-label={label} aria-valuemin={0} aria-valuemax={top}
        aria-valuenow={i} aria-valuetext={format(i)} data-testid={testId}
        className="absolute cursor-pointer outline-none" style={{ left: 135, top: 4, width: 180, height: 12 }}
        onPointerDown={(e) => { (e.target as Element).setPointerCapture?.(e.pointerId); setDrag(at(e.clientX)); }}
        onPointerMove={(e) => drag !== null && setDrag(at(e.clientX))}
        onPointerUp={(e) => commit(at(e.clientX))}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight" || e.key === "ArrowUp") setDrag(Math.min(top, i + 1));
          if (e.key === "ArrowLeft" || e.key === "ArrowDown") setDrag(Math.max(0, i - 1));
        }}
        onKeyUp={(e) => ["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"].includes(e.key) && commit(i)}>
        <div className="absolute rounded-full bg-ctl" style={{ left: 0, right: 0, top: 4, height: 4 }} />
        {top < steps - 1 && (
          <div title={capTitle} className="absolute rounded-r-full" style={{ left: `${(top / (steps - 1)) * 100}%`, right: 0, top: 4,
            height: 4, background: "repeating-linear-gradient(135deg, #2c2c34 0 3px, #1a1a1f 3px 6px)" }} />
        )}
        <div className="absolute rounded-full bg-accent" style={{ left: 0, width: `${frac * 100}%`, top: 4, height: 4 }} />
        <div className="absolute rounded-full bg-white shadow" style={{ left: `calc(${frac * 100}% - 6px)`, top: 0, width: 12, height: 12 }} />
      </div>
      <span className="absolute font-mono text-fg" style={{ right: 0, top: 3, fontSize: 10 }}>{format(i)}</span>
    </div>
  );
}
