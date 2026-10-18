import React, { useCallback, useRef, useState } from "react";

interface Props {
  label: string;
  min: number;
  max: number;
  step?: number;
  value: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
  title?: string;
}

export function ChromaSlider({
  label,
  min,
  max,
  step = 0.01,
  value,
  onChange,
  format,
  title,
}: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);

  const pct = Math.max(0, Math.min(1, (value - min) / (max - min)));

  const handleAt = useCallback(
    (clientX: number) => {
      const el = trackRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      let raw = min + ratio * (max - min);
      if (step) raw = Math.round(raw / step) * step;
      raw = Math.max(min, Math.min(max, raw));
      onChange(parseFloat(raw.toFixed(6)));
    },
    [min, max, step, onChange],
  );

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture(e.pointerId);
    setDragging(true);
    handleAt(e.clientX);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragging) return;
    handleAt(e.clientX);
  };
  const onPointerUp = (e: React.PointerEvent) => {
    setDragging(false);
    (e.target as Element).releasePointerCapture(e.pointerId);
  };

  const display = format ? format(value) : value.toFixed(2);

  return (
    <div className="flex items-center gap-3 py-2" title={title}>
      <label className="font-body text-[12px] text-fg w-[110px] shrink-0">{label}</label>
      <div
        className="flex-1 h-4 flex items-center cursor-pointer touch-none"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        <div
          ref={trackRef}
          className="relative w-full h-[3px] rounded-full bg-border"
        >
          <div
            className="absolute top-0 left-0 h-full rounded-full accent-transition"
            style={{ width: `${pct * 100}%`, backgroundColor: "var(--accent)" }}
          />
          <div
            className="absolute top-1/2 -translate-y-1/2 w-[14px] h-[14px] rounded-full bg-white shadow-md accent-transition"
            style={{
              left: `calc(${pct * 100}% - 7px)`,
              border: "1px solid var(--accent)",
              transform: `translateY(-50%) scale(${dragging ? 1.15 : 1})`,
              transition: "transform 80ms ease-out, border-color 300ms linear",
            }}
          />
        </div>
      </div>
      <div className="font-mono text-[11px] text-fg-dim w-[54px] text-right tabular-nums">
        {display}
      </div>
    </div>
  );
}
