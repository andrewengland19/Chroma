// gui-3 GROUP: pick a group, see its members, set its safety limits (max level,
// max flash rate <= 3 Hz, idle auto-dim). Hardware safety notice per the mockup.
import { useEffect, useState } from "react";
import type { Group } from "../../contracts";
import { useSelect, useStore } from "../../store";
import { Panel } from "../ui";
import { Slider } from "./Slider";

const LEVELS = Array.from({ length: 21 }, (_, i) => i * 0.05);            // 0 … 100 % in 5 % steps
// Drawn to 10 Hz so the 3 Hz safety cap is visible (gui-3 places 3 Hz ~30 % along); never selectable above 3 Hz (§8.1).
const FLASH_HZ = Array.from({ length: 20 }, (_, i) => (i + 1) * 0.5);
const FLASH_CAP = FLASH_HZ.indexOf(3);
const IDLE_MIN: (number | null)[] = [null, 5, 10, 15, 20, 30, 45, 60, 90, 120];

const nearest = (arr: number[], v: number) =>
  arr.reduce((best, x, i) => (Math.abs(x - v) < Math.abs(arr[best] - v) ? i : best), 0);

/** Default selection: a group that has safety limits set (the playbill in the default rig). */
export function defaultGroupId(groups: Group[]): string | null {
  return (groups.find((g) => g.max_level < 1) ?? groups[0])?.id ?? null;
}

export function GroupPanel({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const store = useStore();
  const rig = useSelect((s) => s.rig);
  const [open, setOpen] = useState(false);
  const groups = rig?.groups ?? [];
  const g = groups.find((x) => x.id === selected) ?? null;
  useEffect(() => {
    if (!g && groups.length) onSelect(defaultGroupId(groups)!);
  }, [g, groups, onSelect]);
  // limits only: members are never re-sent from a possibly stale rig
  const save = (patch: Pick<Partial<Group>, "max_level" | "max_flash_hz" | "idle_dim_min">) =>
    g && void store.op("groups.set_limits", { group_id: g.id, ...patch });
  const fx = (id: string) => rig?.fixtures.find((f) => f.id === id);
  const dot = (id: string) => {
    const f = fx(id);
    if (!f || f.status !== "active") return "#5d5d66";
    return rig?.online[id] ? "var(--color-go)" : "var(--color-red)";
  };

  return (
    <Panel style={{ left: 732, top: 45, width: 427, height: 405 }}>
      <div className="label absolute" style={{ left: 14, top: 12 }}>Group</div>
      <div className="absolute" style={{ left: 14, top: 28 }}>
        <button data-testid="group-select" draggable={!!g} aria-haspopup="listbox" aria-expanded={open}
          title="Pick a group — or drag it onto a bank cell"
          onDragStart={(e) => g && e.dataTransfer.setData("application/x-chroma-group", g.id)}
          onClick={() => setOpen((o) => !o)}
          className="flex items-center rounded-[6px] border border-ctl-line bg-ctl font-bold"
          style={{ width: 150, height: 27, paddingLeft: 11, gap: 8, fontSize: 11 }}>
          {g?.id ?? "—"}
          <svg width="7" height="4" viewBox="0 0 7 4"><path d="M0 0 L7 0 L3.5 4 Z" fill="currentColor" /></svg>
        </button>
        {open && (
          <div role="listbox" className="absolute z-10 overflow-y-auto rounded-[6px] border border-line bg-panel shadow-xl"
            style={{ top: 30, width: 150, maxHeight: 220 }}>
            {groups.map((x) => (
              <div key={x.id} role="option" aria-selected={x.id === selected}
                onClick={() => { onSelect(x.id); setOpen(false); }}
                className="flex cursor-pointer items-center hover:bg-ctl" style={{ height: 22, padding: "0 10px", gap: 7, fontSize: 10 }}>
                <span className="rounded-full" style={{ width: 6, height: 6, background: x.color }} />
                {x.id} <span className="text-label">{x.name}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="label absolute" style={{ left: 14, top: 73 }}>Members</div>
      <div data-testid="members" className="absolute flex flex-wrap" style={{ left: 14, top: 89, width: 400, gap: "3.5px 5.5px",
        maxHeight: 82, overflow: "hidden" }}>
        {g && g.members.length === 0 && <span className="text-label" style={{ fontSize: 9 }}>no members yet</span>}
        {g?.members.map((m) => (
          <span key={m} className="flex items-center rounded-full border border-ctl-line bg-ctl font-mono"
            style={{ width: 92, height: 23.5, paddingLeft: 10, gap: 6, fontSize: 9, color: "#b4b4bd" }}>
            <span className="shrink-0 rounded-full" style={{ width: 6, height: 6, background: dot(m) }} />
            <span className="truncate">{fx(m)?.label ?? m}</span>
          </span>
        ))}
      </div>

      <div className="label absolute" style={{ left: 14, top: 178 }}>Safety limits</div>
      {g && (
        <div className="absolute flex flex-col" style={{ left: 15, top: 200, width: 404, gap: 19 }}>
          <Slider label="Max level" testId="max-level" steps={LEVELS.length} value={nearest(LEVELS, g.max_level)}
            format={(i) => `${Math.round(LEVELS[i] * 100)} %`} onCommit={(i) => save({ max_level: LEVELS[i] })} />
          <Slider label="Max flash rate" testId="max-flash" steps={FLASH_HZ.length} maxIndex={FLASH_CAP}
            capTitle="3 Hz is the photosensitivity safety cap — it cannot be raised"
            value={nearest(FLASH_HZ, g.max_flash_hz)}
            format={(i) => `${FLASH_HZ[i]} Hz`} onCommit={(i) => save({ max_flash_hz: FLASH_HZ[i] })} />
          <Slider label="Idle auto-dim" testId="idle-dim" steps={IDLE_MIN.length}
            value={Math.max(0, IDLE_MIN.indexOf(g.idle_dim_min ?? null))}
            format={(i) => (IDLE_MIN[i] == null ? "off" : `after ${IDLE_MIN[i]} min`)}
            onCommit={(i) => save({ idle_dim_min: IDLE_MIN[i] })} />
        </div>
      )}

      <div className="absolute rounded-[6px]" style={{ left: 14, top: 323, width: 398, height: 61,
        border: "1px solid #c0923d", background: "#2a1f12", padding: "9px 12px" }}>
        <div className="font-bold text-warn" style={{ fontSize: 10 }}>Hardware safety lives in the fixture, not here</div>
        <div style={{ fontSize: 8.5, color: "#a9a39a", marginTop: 4, lineHeight: 1.45 }}>
          Current caps and thermal cutoffs are set on the WLED node and by the<br />
          inline thermostat. These limits only add a second layer.
        </div>
      </div>
    </Panel>
  );
}
