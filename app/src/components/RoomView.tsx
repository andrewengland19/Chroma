// Console Room panel: one Space (room) at a time, picked by the tabs in the header.
// Drag a light to move it (fixtures.upsert x/y — the art painter follows); double-click
// the header or an empty spot to configure the Spaces.
import { useRef, useState } from "react";
import { useSelect, useStore } from "../store";
import { Panel } from "./ui";

// Canvas of the Room panel (content-local px), from gui-1.
const W = 300;
const H = 162;
export const DEFAULT_SPACE = "living-room";
const clamp = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 1000) / 1000;

type Drag = { id: string; x: number; y: number; moved: boolean; sx: number; sy: number };
type Light = { id: string; label: string; x: number; y: number; hex: string | null; inRig: boolean };

export function RoomView({ onConfigure }: { onConfigure?: () => void }) {
  const store = useStore();
  const room = useSelect((s) => s.state?.room) ?? [];
  const rig = useSelect((s) => s.rig);
  const canvas = useRef<HTMLDivElement>(null);
  const [picked, setPicked] = useState(DEFAULT_SPACE);
  const [drag, setDragState] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);      // pointer events can outrun re-renders
  const setDrag = (d: Drag | null) => {
    dragRef.current = d;
    setDragState(d);
  };
  // where a dropped light sits until the engine's room patch catches up
  const [placed, setPlaced] = useState<Record<string, { x: number; y: number }>>({});

  const spaces = rig?.spaces?.length ? rig.spaces : null;
  const space = spaces?.find((s) => s.id === picked)?.id ?? spaces?.[0]?.id ?? DEFAULT_SPACE;
  // Before the rig has loaded (or from an engine without Spaces): the live room as is.
  // Otherwise the picked Space's lights: driven ones from the live room, discovered
  // bulbs not yet in the rig drawn dim so they can be placed too.
  let lights: Light[] = room.map((r) => ({ id: r.fixture_id, label: r.label, x: r.x, y: r.y, hex: r.hex, inRig: true }));
  if (spaces && rig) {
    const here = rig.fixtures.filter((f) => (f.space ?? DEFAULT_SPACE) === space && f.status !== "planned");
    const ids = new Set(here.map((f) => f.id));
    const live = new Set(room.map((r) => r.fixture_id));
    lights = [
      ...lights.filter((l) => ids.has(l.id)),
      ...here.filter((f) => !live.has(f.id))
        .map((f) => ({ id: f.id, label: f.label, x: f.x, y: f.y, hex: null, inRig: f.status === "active" })),
    ];
  }
  const lit = lights.filter((l) => l.hex).length;

  // a ratio of the canvas's own rect, so the Shell's CSS zoom doesn't matter
  const at = (e: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    return { x: clamp((e.clientX - r.left) / (r.width || W)), y: clamp((e.clientY - r.top) / (r.height || H)) };
  };

  const drop = async (d: Drag) => {
    const fx = rig?.fixtures.find((f) => f.id === d.id);
    if (!fx) {
      store.toast("warn", "The rig hasn't loaded yet — try again in a moment");
      return;
    }
    setPlaced((p) => ({ ...p, [d.id]: { x: d.x, y: d.y } }));
    await store.op("fixtures.upsert", { fixture: { ...fx, x: d.x, y: d.y } });
    setPlaced(({ [d.id]: _, ...rest }) => rest);
  };

  return (
    <Panel style={{ left: 19, top: 109, width: 324, height: 195 }}>
      <div data-testid="room-header" className="absolute flex items-center" title="Double-click to set up Spaces"
        style={{ left: 11, top: 5, width: W, height: 14, gap: 4 }} onDoubleClick={onConfigure}>
        <span className="label" style={{ marginRight: 2 }}>Room</span>
        {spaces ? (
          <div role="tablist" aria-label="Spaces" className="flex min-w-0 flex-1" style={{ gap: 3 }}>
            {spaces.map((s) => (
              <button key={s.id} role="tab" aria-selected={s.id === space} onClick={() => setPicked(s.id)}
                className={`truncate rounded-[4px] font-bold ${s.id === space ? "bg-accent text-white" : "text-dim hover:text-fg"}`}
                style={{ height: 13, padding: "0 6px", fontSize: 7.5, letterSpacing: "0.02em", maxWidth: 110 }}>
                {s.name}
              </button>
            ))}
          </div>
        ) : (
          <span className="label normal-case">· live</span>
        )}
      </div>
      <div ref={canvas} data-testid="room-canvas" className="absolute overflow-hidden rounded-[7px] border border-line bg-well"
        style={{ left: 11, top: 21, width: W, height: H }}
        onDoubleClick={(e) => e.target === e.currentTarget && onConfigure?.()}>
        {lights.map((l) => {
          const pos = drag?.id === l.id ? drag : placed[l.id] ?? l;
          const x = pos.x * W;
          const y = pos.y * H;
          const c = l.hex ?? "#3a3a42";
          return (
            <div key={l.id} data-testid={`room-${l.id}`} style={{ opacity: l.inRig ? 1 : 0.45 }}>
              {l.hex && (
                <div className="pointer-events-none absolute rounded-full"
                  style={{ left: x - 38, top: y - 38, width: 76, height: 76,
                    background: `radial-gradient(circle, ${c}80 0%, ${c}38 38%, transparent 70%)` }} />
              )}
              <div className="absolute rounded-full" title={l.inRig ? l.label : `${l.label} — not in the rig yet (Setup)`}
                style={{ left: x - 7.5, top: y - 7.5, width: 15, height: 15, background: c, border: "1.3px solid #f3f3f6",
                  cursor: drag?.id === l.id ? "grabbing" : "grab", touchAction: "none" }}
                onPointerDown={(e) => {
                  e.currentTarget.setPointerCapture?.(e.pointerId);
                  setDrag({ id: l.id, x: l.x, y: l.y, moved: false, sx: e.clientX, sy: e.clientY });
                }}
                onPointerMove={(e) => {
                  const d = dragRef.current;
                  if (d?.id !== l.id) return;
                  const moved = d.moved || Math.hypot(e.clientX - d.sx, e.clientY - d.sy) > 3;
                  setDrag({ ...d, ...(moved ? at(e) : {}), moved });
                }}
                onPointerUp={() => {
                  const d = dragRef.current;
                  if (d?.id !== l.id) return;
                  setDrag(null);
                  if (d.moved) void drop(d);
                }}
                onPointerCancel={() => setDrag(null)} />
              <div className="pointer-events-none absolute whitespace-nowrap text-dim" style={{ left: x, top: y + 10, fontSize: 8, transform: "translateX(-50%)" }}>
                {l.label}
              </div>
            </div>
          );
        })}
        {spaces && lights.length === 0 && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-dim" style={{ fontSize: 9 }}>
            No lights here — double-click to assign some
          </div>
        )}
        <div className="pointer-events-none absolute text-dim" style={{ right: 5, bottom: 2, fontSize: 7.5 }}>{`${lit} / ${lights.length} lights`}</div>
      </div>
    </Panel>
  );
}
