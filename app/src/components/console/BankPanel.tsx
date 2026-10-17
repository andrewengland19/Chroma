// The fader bank (gui-1, right): BANK tabs, FADER FUNCTION, 8 channel strips (scribble
// strip, ASSIGN, fader with ghost marker, value, FLASH, number) and MASTER with BLACKOUT.
// Everything shown comes from the State; controls only send intents (§5.1).
import { useEffect, useRef, useState } from "react";
import type { ChannelState, FaderFn, Fixture, Intent } from "../../contracts";

type Bank = ChannelState["bank"];
import { liveColor, tint } from "../../lib/color";
import { useSelect, useStore } from "../../store";
import { Panel } from "../ui";

// Geometry in canvas px, measured from gui-1 (strip-local unless noted).
const PANEL = { left: 358, top: 108, width: 801, height: 501 };
const STRIP = { left: 15, top: 39, width: 81, height: 444, pitch: 87 };
const MASTER = { left: 714, width: 72 };
const TRACK = { top: 75, height: 246, width: 5.5 };
const CAP = { width: 27, height: 23 };
const FLASH_D = 50;
const SOLO = "light-";   // id prefix of the one-light groups made by the ASSIGN picker

const BANKS: Bank[] = ["A", "B", "C"];
const FNS: { fn: FaderFn; label: string }[] = [
  { fn: "level", label: "LEVEL" }, { fn: "hue", label: "HUE" }, { fn: "sat", label: "SAT" },
];

export function fnValue(c: { level: number; hue: number; sat: number }, fn: FaderFn): number {
  return fn === "level" ? c.level : fn === "hue" ? (c.hue % 360) / 360 : c.sat;
}

function fnText(c: ChannelState, fn: FaderFn): string {
  return fn === "hue" ? `${Math.round(c.hue)}°` : `${Math.round(fnValue(c, fn) * 100)}%`;
}

/** Vertical fader: drag (or click) anywhere on the track area to set 0..1. */
function Fader({ value, color, ghost, onChange, cx, testId, label, scale }: {
  value: number; color: string; ghost: boolean; onChange: (v: number) => void;
  cx: number; testId: string; label: string; scale: [string, string, string];
}) {
  const area = useRef<HTMLDivElement>(null);
  const last = useRef<number | null>(null);
  const send = (clientY: number) => {
    const r = area.current?.getBoundingClientRect();
    if (!r || r.height <= 0) return;
    const v = Math.min(1, Math.max(0, (r.bottom - clientY) / r.height));
    const q = Math.round(v * 1000) / 1000;
    if (last.current !== null && Math.abs(q - last.current) < 0.002) return;
    last.current = q;
    onChange(q);
  };
  const y = TRACK.top + (1 - value) * TRACK.height;
  return (
    <>
      {/* scale: 11 ticks, labels at 100 / 50 / 0 */}
      {Array.from({ length: 11 }, (_, i) => {
        const ty = TRACK.top + (i / 10) * TRACK.height;
        const lbl = i === 0 ? scale[0] : i === 5 ? scale[1] : i === 10 ? scale[2] : null;
        return (
          <div key={i}>
            {lbl && (
              <div className="absolute text-right font-mono text-label" style={{ left: 0, width: 16, top: ty - 4, fontSize: 6.5, lineHeight: "8px" }}>{lbl}</div>
            )}
            <div className="absolute" style={{ left: cx - 20, top: ty - 0.4, width: 7, height: 0.8, background: "#45454f" }} />
          </div>
        );
      })}
      <div className="absolute rounded-full bg-well" style={{ left: cx - TRACK.width / 2, top: TRACK.top, width: TRACK.width, height: TRACK.height }} />
      <div data-testid={`${testId}-fill`} className="absolute rounded-full"
        style={{ left: cx - TRACK.width / 2, top: y, width: TRACK.width, height: TRACK.top + TRACK.height - y, background: color }} />
      <div data-testid={`${testId}-cap`} data-ghost={ghost || undefined}
        title={ghost ? "Board fader not picked up — move it through this point" : undefined}
        className="pointer-events-none absolute flex items-center justify-center rounded-[3.5px]"
        style={{ left: cx - CAP.width / 2, top: y - CAP.height / 2, width: CAP.width, height: CAP.height,
          background: ghost ? "transparent" : "#393943", opacity: ghost ? 0.85 : 1,
          border: ghost ? "1.2px dashed #9a9aa6" : "1px solid #585864",
          boxShadow: ghost ? "none" : "0 1px 2px rgba(0,0,0,.5)" }}>
        <div style={{ width: 19, height: 1.4, borderRadius: 1, background: ghost ? "#9a9aa6" : "#f3f3f6" }} />
      </div>
      <div ref={area} role="slider" aria-label={label} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.round(value * 100)} data-testid={testId} tabIndex={0}
        className="absolute touch-none" style={{ left: cx - 18, top: TRACK.top, width: 36, height: TRACK.height, cursor: "ns-resize" }}
        onPointerDown={(e) => {
          last.current = null;
          (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
          send(e.clientY);
        }}
        onPointerMove={(e) => { if (e.buttons & 1) send(e.clientY); }}
        onKeyDown={(e) => {
          const step = e.shiftKey ? 0.1 : 0.01;
          if (e.key === "ArrowUp") onChange(Math.min(1, value + step));
          if (e.key === "ArrowDown") onChange(Math.max(0, value - step));
        }} />
    </>
  );
}

function AssignPicker({ ch, current, onClose, anchor }: {
  ch: number; current: string | null; onClose: () => void; anchor: React.RefObject<HTMLElement | null>;
}) {
  const store = useStore();
  const rigGroups = useSelect((s) => s.rig?.groups) ?? [];
  const fixtures = useSelect((s) => s.rig?.fixtures) ?? [];
  const groups = rigGroups.filter((g) => !g.id.startsWith(SOLO));
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const off = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !anchor.current?.contains(t)) onClose();   // its own button toggles
    };
    window.addEventListener("pointerdown", off, true);
    return () => window.removeEventListener("pointerdown", off, true);
  }, [onClose, anchor]);
  const assign = (group_id: string | null) => void store.intent({ intent: "assign", ch, group_id } as Intent);
  const pick = (group_id: string | null) => {
    assign(group_id);
    onClose();
  };
  // A channel binds to a group, so a single light gets a one-member group of its own (made once, reused).
  const pickLight = async (f: Fixture) => {
    onClose();
    const solo = rigGroups.find((g) => g.members.length === 1 && g.members[0] === f.id);
    if (solo) return assign(solo.id);
    const r = await store.op("groups.upsert", { group: {
      id: SOLO + f.id, name: f.label.slice(0, 16), color: f.color ?? "#9a9aa6",
      members: [f.id], max_level: 1, max_flash_hz: 3, idle_dim_min: null,
    } });
    if (r.ok) assign(SOLO + f.id);
  };
  return (
    <div ref={ref} role="listbox" aria-label={`Assign channel ${ch}`} data-testid={`assign-picker-${ch}`}
      className="absolute z-20 rounded-[6px] border border-ctl-line bg-ctl shadow-lg"
      style={{ left: 6, top: 64, width: 110, padding: 3, maxHeight: 260, overflowY: "auto" }}>
      {[...groups.map((g) => ({ id: g.id as string | null, name: g.name, color: g.color })), { id: null, name: "None", color: "#5c5c68" }].map((g) => (
        <button key={g.id ?? "none"} role="option" aria-selected={g.id === current} onClick={() => pick(g.id)}
          className={`flex w-full items-center rounded-[4px] text-left hover:bg-panel ${g.id === current ? "text-fg" : "text-dim"}`}
          style={{ fontSize: 9, padding: "3px 5px", gap: 6 }}>
          <span className="rounded-full" style={{ width: 6, height: 6, background: g.color }} />{g.name}
        </button>
      ))}
      {fixtures.some((f) => f.status !== "not_added") && (
        <div className="label" style={{ fontSize: 7, padding: "5px 5px 2px" }}>Lights</div>
      )}
      {fixtures.filter((f) => f.status !== "not_added").map((f) => {
        const id = rigGroups.find((g) => g.members.length === 1 && g.members[0] === f.id)?.id;
        return (
          <button key={f.id} role="option" aria-selected={id !== undefined && id === current} onClick={() => void pickLight(f)}
            data-testid={`assign-light-${f.id}`}
            className={`flex w-full items-center rounded-[4px] text-left hover:bg-panel ${id !== undefined && id === current ? "text-fg" : "text-dim"}`}
            style={{ fontSize: 9, padding: "3px 5px", gap: 6 }}>
            <span className="rounded-full" style={{ width: 6, height: 6, background: f.color ?? "#9a9aa6" }} />{f.label}
          </button>
        );
      })}
    </div>
  );
}

function Strip({ c, index, fn }: { c: ChannelState; index: number; fn: FaderFn }) {
  const store = useStore();
  const group = useSelect((s) => s.rig?.groups.find((g) => g.id === c.group_id));
  const [picking, setPicking] = useState(false);
  const color = liveColor(c.hue, c.sat, 0.8);
  const ring = liveColor(c.hue, c.sat, 0.93);
  const flashDown = (down: boolean) => void store.intent({ intent: "flash", ch: c.ch, down });
  const holding = useRef(false);
  const assignBtn = useRef<HTMLButtonElement>(null);
  useEffect(() => () => {
    // the strip goes away (bank switch) while FLASH is held: never leave a flash stuck on
    if (holding.current) void store.intent({ intent: "flash", ch: c.ch, down: false });
  }, [store, c.ch]);
  const release = () => {
    if (holding.current) {
      holding.current = false;
      flashDown(false);
    }
  };
  // Number keys 1–8 (row or keypad) flash the lane of that number, same as holding its FLASH button.
  useEffect(() => {
    const typing = (t: EventTarget | null) =>
      t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
    const down = (e: KeyboardEvent) => {
      if (e.key !== String(c.ch) || e.repeat || e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
      if (holding.current) return;
      holding.current = true;
      flashDown(true);
    };
    const up = (e: KeyboardEvent) => { if (e.key === String(c.ch)) release(); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", release);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", release);
    };
  });
  const cx = 41;
  return (
    <div data-testid={`strip-${c.ch}`} className="absolute rounded-[7px] border border-line bg-ctl"
      style={{ left: STRIP.left + index * STRIP.pitch, top: STRIP.top, width: STRIP.width, height: STRIP.height }}>
      <div data-testid={`scribble-${c.ch}`} className="absolute flex items-center justify-center truncate rounded-[3px] bg-well font-mono"
        style={{ left: 6.5, top: 7.5, width: 68, height: 24.5, fontSize: 10, letterSpacing: "0.04em", color: group ? "#a8e6bd" : "#4b5a50" }}>
        {group ? group.name.toUpperCase() : "—"}
      </div>
      <button ref={assignBtn} aria-label={`Assign channel ${c.ch}`} data-testid={`assign-${c.ch}`} aria-expanded={picking}
        className="absolute flex items-center" style={{ left: 18, top: 43.5, gap: 5 }}
        onClick={() => {
          if (!picking) void store.intent({ intent: "assign", ch: c.ch });   // select (X2)
          setPicking((p) => !p);
        }}>
        <span className="rounded-full" style={{ width: 16.5, height: 16.5, border: "1.3px solid #6a6a76", background: picking ? "var(--color-accent)" : "transparent" }} />
        <span className="font-bold text-label" style={{ fontSize: 6, letterSpacing: "0.03em" }}>ASSIGN</span>
      </button>
      {picking && <AssignPicker ch={c.ch} current={c.group_id} anchor={assignBtn} onClose={() => setPicking(false)} />}
      <Fader value={fnValue(c, fn)} color={color} ghost={!c.engaged} cx={cx} testId={`fader-${c.ch}`}
        label={`Channel ${c.ch} ${fn}`} scale={fn === "hue" ? ["360", "180", "0"] : ["100", "50", "0"]}
        onChange={(v) => void store.intent({ intent: "fader", ch: c.ch, v })} />
      <div data-testid={`value-${c.ch}`} className="absolute text-center text-fg" style={{ left: 0, width: STRIP.width, top: 331, fontSize: 9 }}>
        {fnText(c, fn)}
      </div>
      <button data-testid={`flash-${c.ch}`} aria-pressed={c.flash} aria-label={`Flash channel ${c.ch}`}
        className="absolute flex touch-none items-center justify-center rounded-full font-bold"
        style={{ left: cx - FLASH_D / 2, top: 360, width: FLASH_D, height: FLASH_D, fontSize: 7.5,
          border: `1.8px solid ${ring}`, background: c.flash ? ring : tint(ring, 0.25, "#25252d"),
          color: c.flash ? "#1d1d23" : "#ececf1",
          boxShadow: c.flash ? `0 0 22px 8px ${ring}55` : "none" }}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
          holding.current = true;
          flashDown(true);
        }}
        onPointerUp={release} onPointerCancel={release} onLostPointerCapture={release}>
        FLASH
      </button>
      <div className="absolute text-center font-bold text-label" style={{ left: 0, width: STRIP.width, top: 422, fontSize: 8 }}>{c.ch}</div>
    </div>
  );
}

function MasterStrip() {
  const store = useStore();
  const master = useSelect((s) => s.state?.master) ?? 1;
  const blackout = useSelect((s) => s.state?.blackout) ?? false;
  // Hold 0 for blackout, released back to what it was; lane keys keep flashing meanwhile.
  const was = useRef<boolean | null>(null);
  const live = useRef(blackout);
  live.current = blackout;
  useEffect(() => {
    const typing = (t: EventTarget | null) =>
      t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
    const release = () => {
      if (was.current === null) return;
      const prior = was.current;
      was.current = null;
      if (!prior) void store.intent({ intent: "blackout", on: false });
    };
    const down = (e: KeyboardEvent) => {
      if (e.key !== "0" || e.repeat || e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || was.current !== null) return;
      was.current = live.current;
      if (!live.current) void store.intent({ intent: "blackout", on: true });
    };
    const up = (e: KeyboardEvent) => { if (e.key === "0") release(); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", release);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", release);
      release();
    };
  }, [store]);
  return (
    <div data-testid="strip-master" className="absolute rounded-[7px] border border-line bg-ctl"
      style={{ left: MASTER.left, top: STRIP.top, width: MASTER.width, height: STRIP.height }}>
      <div className="absolute flex items-center justify-center rounded-[3px] bg-well font-mono"
        style={{ left: 6.5, top: 7.5, width: 59, height: 24.5, fontSize: 10, letterSpacing: "0.04em", color: "#a8e6bd" }}>MASTER</div>
      <Fader value={master} color="#a8a8ad" ghost={false} cx={36} testId="fader-master" label="Master"
        scale={["", "", ""]} onChange={(v) => void store.intent({ intent: "fader", ch: "master", v })} />
      <div data-testid="value-master" className="absolute text-center text-fg" style={{ left: 0, width: MASTER.width, top: 331, fontSize: 9 }}>
        {Math.round(master * 100)}%
      </div>
      <BlackoutButton testId="blackout-master" on={blackout}
        style={{ left: 9, top: 366, width: 54, height: 39, fontSize: 7, borderRadius: 6 }} />
    </div>
  );
}

export function BlackoutButton({ on, style, testId }: { on: boolean; style: React.CSSProperties; testId: string }) {
  const store = useStore();
  return (
    <button data-testid={testId} aria-pressed={on} title={on ? "Blackout is on — click to restore" : "Blackout"}
      className="absolute flex items-center justify-center font-bold"
      style={{ border: "1.5px solid var(--color-red)", color: on ? "#fff" : "var(--color-red)",
        background: on ? "var(--color-red)" : "#2a1e24", letterSpacing: "0.01em", ...style }}
      onClick={() => void store.intent({ intent: "blackout", on: !on })}>
      BLACKOUT
    </button>
  );
}

function SegBtn({ on, label, onClick, style, testId }: { on: boolean; label: string; onClick: () => void; style: React.CSSProperties; testId: string }) {
  return (
    <button role="radio" aria-checked={on} data-testid={testId} onClick={onClick}
      className={`absolute flex items-center justify-center rounded-[5px] font-bold ${on ? "text-white" : "text-dim hover:text-fg"}`}
      style={{ top: 8, height: 18.5, fontSize: 8.5, letterSpacing: "0.02em", ...style }}>
      {label}
    </button>
  );
}

export function BankPanel() {
  const store = useStore();
  const bank = useSelect((s) => s.state?.bank) ?? "A";
  const fn = useSelect((s) => s.state?.fader_fn) ?? "level";
  const channels = useSelect((s) => s.state?.channels) ?? [];
  const strips = channels.filter((c) => c.bank === bank).sort((a, b) => a.ch - b.ch);
  return (
    <Panel style={PANEL}>
      <div className="label absolute" style={{ left: 13, top: 12 }}>Bank</div>
      <div role="radiogroup" aria-label="Bank" data-testid="bank">
        {BANKS.map((b, i) => (
          <SegBtn key={b} testId={`bank-${b}`} on={b === bank} label={b}
            onClick={() => void store.intent({ intent: "bank", bank: b })}
            style={{ left: 47 + i * 33, width: 27.5, fontSize: 9.5,
              background: b === bank ? "var(--color-accent)" : "var(--color-ctl)",
              border: b === bank ? "none" : "1px solid var(--color-ctl-line)" }} />
        ))}
      </div>
      <div className="label absolute" style={{ left: 188, top: 12 }}>Fader function</div>
      <div role="radiogroup" aria-label="Fader function" data-testid="fader-fn">
        {FNS.map((f, i) => (
          <SegBtn key={f.fn} testId={`fn-${f.fn}`} on={f.fn === fn} label={f.label}
            onClick={() => void store.intent({ intent: "fader_fn", fn: f.fn })}
            style={{ left: 270 + i * 52.6, width: 47.5,
              background: f.fn === fn ? "#2c2650" : "var(--color-ctl)",
              border: f.fn === fn ? "1.3px solid var(--color-accent)" : "1px solid var(--color-ctl-line)" }} />
        ))}
      </div>
      <div className="absolute text-label" style={{ right: 14, top: 12, fontSize: 7.5 }}>board and screen are the same console</div>
      {strips.map((c, i) => <Strip key={`${c.bank}${c.ch}`} c={c} index={i} fn={fn} />)}
      <MasterStrip />
    </Panel>
  );
}
