// COLOR (hue/sat knobs + palette) and CUES (8 pads), gui-1 left column.
// Knobs, palette and cues act on the engine's last-touched channel (§5.3).
import { useRef } from "react";
import { hexToHsv, hueDist } from "../../lib/color";
import { tint } from "../../lib/color";
import { useSelect, useStore } from "../../store";
import { Panel } from "../ui";
import { SCENE_SLOTS, type SceneSummary, type ScenesPane } from "./scenes";
import { useHold } from "./useHold";

const PX_PER_DETENT = 4;                 // knob drag: 4 px = one encoder detent (5° / 2 %)
const WHEEL_PER_DETENT = 40;             // wheel/trackpad delta per detent
// Cue pad colours by slot (gui-1); fixed, unlike the editable palette.
export const CUE_COLORS = ["#e5484d", "#f5a05b", "#f2c94c", "#4ade80", "#2dd4bf", "#3b82f6", "#a855f7", "#f472b6"];

function useLastTouched() {
  return useSelect((s) => {
    const lt = s.state?.last_touched;
    return lt ? s.state?.channels.find((c) => c.bank === lt.bank && c.ch === lt.ch) ?? null : null;
  });
}

function Knob({ cx, value, label, text, onDetents, testId }: {
  cx: number; value: number | null; label: string; text: string;
  onDetents: (n: number) => void; testId: string;
}) {
  const acc = useRef(0);
  const wheel = useRef(0);
  const lastY = useRef<number | null>(null);
  const turn = (n: number) => { if (value !== null) onDetents(n); };   // no channel: nothing to turn
  const R = 28.8, r = 22.8, cy = 55;
  const a0 = -135, sweep = 270;
  const a = a0 + sweep * (value ?? 0);
  const pt = (deg: number, rad: number) => {
    const t = ((deg - 90) * Math.PI) / 180;
    return [R + 2 + rad * Math.cos(t), R + 2 + rad * Math.sin(t)];
  };
  const arc = (from: number, to: number) => {
    const [x0, y0] = pt(from, R);
    const [x1, y1] = pt(to, R);
    return `M ${x0} ${y0} A ${R} ${R} 0 ${to - from > 180 ? 1 : 0} 1 ${x1} ${y1}`;
  };
  const [px0, py0] = pt(a, 6);
  const [px1, py1] = pt(a, 17);
  const size = 2 * R + 4;
  return (
    <>
      <div role="slider" aria-label={label} aria-valuetext={text} data-testid={testId} tabIndex={0}
        className="absolute touch-none" style={{ left: cx - size / 2, top: cy - size / 2, width: size, height: size, cursor: "ns-resize" }}
        onPointerDown={(e) => {
          (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
          lastY.current = e.clientY;
          acc.current = 0;
        }}
        onPointerMove={(e) => {
          if (!(e.buttons & 1) || lastY.current === null) return;
          acc.current += lastY.current - e.clientY;    // up = more
          lastY.current = e.clientY;
          const n = Math.trunc(acc.current / PX_PER_DETENT);
          if (n) {
            acc.current -= n * PX_PER_DETENT;
            turn(n);
          }
        }}
        onPointerUp={() => { lastY.current = null; }}
        onWheel={(e) => {
          if (!e.deltaY) return;
          wheel.current -= e.deltaY;
          if (Math.abs(wheel.current) >= WHEEL_PER_DETENT) {      // a mouse notch = one detent
            turn(Math.sign(wheel.current));
            wheel.current = 0;
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp" || e.key === "ArrowRight") turn(e.shiftKey ? 5 : 1);
          if (e.key === "ArrowDown" || e.key === "ArrowLeft") turn(e.shiftKey ? -5 : -1);
        }}>
        <svg width={size} height={size} className="pointer-events-none">
          <path d={arc(a0, a0 + sweep)} stroke="#3a3a44" strokeWidth={2.4} fill="none" strokeLinecap="round" />
          {value !== null && value > 0.002 && (
            <path d={arc(a0, a)} stroke="var(--color-accent)" strokeWidth={2.4} fill="none" strokeLinecap="round" />
          )}
          <circle cx={R + 2} cy={R + 2} r={r} fill="#25252d" stroke="#34343e" strokeWidth={1} />
          {value !== null && <line x1={px0} y1={py0} x2={px1} y2={py1} stroke="#f3f3f6" strokeWidth={2} strokeLinecap="round" />}
        </svg>
      </div>
      <div className="absolute text-center font-bold text-dim" style={{ left: cx - 30, width: 60, top: 92, fontSize: 8 }}>{label.toUpperCase()}</div>
      <div data-testid={`${testId}-value`} className="absolute text-center text-fg" style={{ left: cx - 30, width: 60, top: 103, fontSize: 9 }}>{text}</div>
    </>
  );
}

function Swatch({ i, hex, selected }: { i: number; hex: string; selected: boolean }) {
  const store = useStore();
  const hold = useHold(
    () => void store.intent({ intent: "palette_apply", i }),
    () => void store.intent({ intent: "palette_store", i }),
  );
  return (
    <button data-testid={`swatch-${i}`} aria-label={`Palette ${i + 1}`} aria-pressed={selected} {...hold}
      title="tap = apply · hold = store"
      className="absolute rounded-[4.5px]"
      style={{ left: 195.5 + (i % 4) * 30, top: 34 + Math.floor(i / 4) * 30, width: 23.5, height: 23.5, background: hex,
        border: selected ? "1.5px solid #f3f3f6" : "none" }} />
  );
}

export function ColorPanel() {
  const store = useStore();
  const c = useLastTouched();
  const palette = useSelect((s) => s.state?.palette) ?? [];
  const sel = c ? palette.findIndex((hx) => {
    const p = hexToHsv(hx);
    return hueDist(p.h, c.hue) < 8 && Math.abs(p.s - c.sat) < 0.12;   // "this swatch is (about) the colour"
  }) : -1;
  return (
    <Panel style={{ left: 19, top: 314, width: 324, height: 145 }}>
      <div className="label absolute" style={{ left: 11, top: 8 }}>Color</div>
      <Knob cx={63} testId="knob-hue" label="Hue" value={c ? (c.hue % 360) / 360 : null}
        text={c ? `${Math.round(c.hue)}°` : "—"} onDetents={(delta) => void store.intent({ intent: "hue", delta })} />
      <Knob cx={146.5} testId="knob-sat" label="Sat" value={c ? c.sat : null}
        text={c ? `${Math.round(c.sat * 100)}%` : "—"} onDetents={(delta) => void store.intent({ intent: "sat", delta })} />
      <div className="label absolute whitespace-nowrap" style={{ left: 195.5, top: 18 }}>
        Palette <span className="normal-case" style={{ marginLeft: 4, letterSpacing: 0, fontSize: 6.5, color: "var(--color-dim)" }}>tap = apply · hold = store</span>
      </div>
      {palette.map((hx, i) => <Swatch key={i} i={i} hex={hx} selected={i === sel} />)}
      <div className="absolute text-label" style={{ left: 195.5, top: 98.5, fontSize: 7.5 }}>acts on last-touched channel</div>
    </Panel>
  );
}

function CuePad({ i, name, stored, active }: { i: number; name: string; stored: boolean; active: boolean }) {
  const store = useStore();
  const col = CUE_COLORS[i];
  const hold = useHold(
    () => void store.intent({ intent: "cue_recall", i }),
    () => void store.intent({ intent: "cue_store", i }),
  );
  return (
    <button data-testid={`cue-${i}`} aria-pressed={active} aria-label={`Cue ${i + 1} ${name}`} {...hold}
      title={stored ? "tap = recall · shift + tap or hold = store" : "empty — shift + tap or hold to store the current look"}
      className="absolute rounded-[6px]"
      style={{ left: 11 + (i % 4) * 76.8, top: 27 + Math.floor(i / 4) * 49.8, width: 71, height: 42,
        border: `1.3px ${stored ? "solid" : "dashed"} ${active ? col : stored ? col : tint(col, 0.5)}`,
        background: active ? col : stored ? tint(col, 0.2) : "transparent" }}>
      <span className="absolute font-mono font-bold" style={{ left: 6, top: 4.5, fontSize: 7.5, color: active ? "#1d1d23" : "#6a6a76" }}>C{i + 1}</span>
      <span className="absolute inset-x-0 text-center font-bold" style={{ top: 18, fontSize: 9.5, color: active ? "#1d1d23" : stored ? "#ececf1" : "#6a6a76" }}>{name}</span>
    </button>
  );
}

function ScenePad({ i, scene, active, onRecall }: { i: number; scene: SceneSummary; active: boolean; onRecall: () => void }) {
  const col = CUE_COLORS[i];
  return (
    <button data-testid={`scene-${i}`} aria-pressed={active} aria-label={`Scene ${scene.name}`} onClick={onRecall}
      title={`tap = recall ${scene.name}`}
      className="absolute rounded-[6px]"
      style={{ left: 11 + (i % 4) * 76.8, top: 27 + Math.floor(i / 4) * 49.8, width: 71, height: 42,
        border: `1.3px solid ${col}`, background: active ? col : tint(col, 0.2) }}>
      <span className="absolute flex" style={{ left: 6, top: 5, gap: 2 }}>
        {scene.colors.slice(0, 4).map((c, k) => (
          <span key={k} className="rounded-full" style={{ width: 6, height: 6, background: c, border: "1px solid #1d1d23" }} />
        ))}
      </span>
      <span className="absolute inset-x-1 truncate text-center font-bold" style={{ top: 18, fontSize: 9.5, color: active ? "#1d1d23" : "#ececf1" }}>
        {scene.name.replace(/^agent\./, "")}
      </span>
    </button>
  );
}

export function CuesPanel({ pane, onConfigure }: { pane: ScenesPane; onConfigure?: () => void }) {
  const cues = useSelect((s) => s.state?.cues) ?? [];
  const active = useSelect((s) => s.state?.cue_active);
  const scenesMode = pane.mode === "scenes";
  const many = pane.all.length > SCENE_SLOTS;
  return (
    <Panel style={{ left: 19, top: 469, width: 324, height: 140 }}
      onDoubleClick={(e) => scenesMode && many && !(e.target as HTMLElement).closest("button") && onConfigure?.()}>
      <div className="absolute flex items-center" style={{ left: 11, top: 5, height: 14, gap: 4 }}>
        <span className="label" style={{ marginRight: 2 }}>{scenesMode ? "Scenes" : "Cues"}</span>
        <div role="tablist" aria-label="Cues or Scenes" className="flex" style={{ gap: 3 }}>
          {(["cues", "scenes"] as const).map((m) => (
            <button key={m} role="tab" aria-selected={m === pane.mode} onClick={() => pane.setMode(m)}
              className={`rounded-[4px] font-bold ${m === pane.mode ? "bg-accent text-white" : "text-dim hover:text-fg"}`}
              style={{ height: 13, padding: "0 6px", fontSize: 7.5, letterSpacing: "0.02em" }}>
              {m === "cues" ? "Cues" : "Scenes"}
            </button>
          ))}
        </div>
      </div>
      {scenesMode ? (
        <>
          {pane.visible.map((sc, i) => <ScenePad key={sc.name} i={i} scene={sc} active={sc.name === pane.active} onRecall={() => void pane.recall(sc.name)} />)}
          {pane.loaded && pane.visible.length === 0 && (
            <div className="pointer-events-none absolute inset-x-0 text-center text-dim" style={{ top: 62, fontSize: 9 }}>
              {pane.all.length ? "No scenes chosen — double-click to pick some" : "No saved scenes — save one from the Agent tab"}
            </div>
          )}
          <div className="absolute text-label" style={{ left: 11, top: 120.5, fontSize: 7.5 }}>
            {many ? `showing ${pane.visible.length} of ${pane.all.length} · double-click to choose` : "tap a scene to recall it"}
          </div>
        </>
      ) : (
        <>
          {cues.map((c) => <CuePad key={c.id} i={c.id} name={c.name} stored={c.stored} active={c.id === active} />)}
          <div className="absolute text-label" style={{ left: 11, top: 120.5, fontSize: 7.5 }}>shift + tap stores the current look</div>
        </>
      )}
    </Panel>
  );
}
