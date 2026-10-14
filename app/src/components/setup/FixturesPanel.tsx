// gui-3 FIXTURES: one row per fixture (planned/not-added series collapse into one row,
// e.g. "Playbill 01–12"), Discover LIFX, + Add fixture, Test.
import { useState } from "react";
import type { Fixture } from "../../contracts";
import { useSelect, useStore, type Rig } from "../../store";
import { Panel } from "../ui";
import { AddFixtureForm } from "./AddFixtureForm";

const TYPE_STYLE: Record<string, { label: string; color: string; text: string; bg: string }> = {
  lifx: { label: "LIFX", color: "#3d8ef0", text: "#4d9bf5", bg: "rgba(61,142,240,0.10)" },
  wled: { label: "WLED", color: "#d9a12a", text: "#e5a92e", bg: "rgba(217,161,42,0.10)" },
  virtual: { label: "VIRT", color: "#8b5cf6", text: "#a78bfa", bg: "rgba(139,92,246,0.12)" },
};

export interface FixtureRow {
  key: string;
  label: string;
  members: Fixture[];
  type: Fixture["type"];
  status: Fixture["status"];
  klass: Fixture["class"];
  address: string;
  groups: string[];
  color: string | null;
  online: boolean;
}

/** Collapse fixtures sharing a `series` (only when not active) into one table row. */
export function tableRows(rig: Rig, roomHex: Record<string, string | null>): FixtureRow[] {
  const rows: FixtureRow[] = [];
  const bySeries = new Map<string, FixtureRow>();
  const groupsOf = (id: string) => rig.groups.filter((g) => g.members.includes(id)).map((g) => g.id);
  for (const f of rig.fixtures) {
    const series = f.status !== "active" ? f.series : undefined;
    const existing = series ? bySeries.get(series) : undefined;
    if (existing) {
      // a collapsed series lists only the groups ALL of its members are in (gui-3: "playbill", not "pb-1-4")
      existing.members.push(f);
      const mine = groupsOf(f.id);
      existing.groups = existing.groups.filter((g) => mine.includes(g));
      continue;
    }
    const address = f.status === "planned" ? "planned" : f.status === "not_added" ? "not added yet"
      : f.type === "virtual" ? "in-app" : f.ip ?? "";
    const row: FixtureRow = {
      key: series ?? f.id, label: series ?? f.label, members: [f], type: f.type, status: f.status, klass: f.class,
      address, groups: groupsOf(f.id), online: !!rig.online[f.id],
      color: f.status === "active" ? roomHex[f.id] ?? f.color ?? null : null,
    };
    rows.push(row);
    if (series) bySeries.set(series, row);
  }
  return rows;
}

function StatusDot({ row }: { row: FixtureRow }) {
  const c = row.status !== "active" ? "#5d5d66" : row.type === "virtual" ? "var(--color-warn)"
    : row.online ? "var(--color-go)" : "var(--color-red)";
  return <span className="absolute rounded-full" style={{ left: 7, top: 12.5, width: 6, height: 6, background: c }} />;
}

export function FixturesPanel() {
  const store = useStore();
  const rig = useSelect((s) => s.rig);
  const room = useSelect((s) => s.state?.room);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const roomHex = Object.fromEntries((room ?? []).map((r) => [r.fixture_id, r.hex]));
  const rows = rig ? tableRows(rig, roomHex) : [];

  const discover = async () => {
    setBusy(true);
    await store.op("fixtures.discover");
    setBusy(false);
  };
  const add = (f: Fixture) => void store.op("fixtures.upsert", { fixture: { ...f, status: "active" } });

  return (
    <Panel style={{ left: 19, top: 45, width: 697, height: 405 }}>
      <div className="label absolute" style={{ left: 14, top: 12 }}>Fixtures</div>
      <button onClick={discover} disabled={busy} data-testid="discover"
        className="absolute rounded-[6px] border border-ctl-line bg-ctl font-semibold hover:border-dim"
        style={{ left: 525, top: 7, width: 79, height: 23, fontSize: 9.5 }}>
        {busy ? "Discovering…" : "Discover LIFX"}
      </button>
      <button onClick={() => setAdding(true)} data-testid="add-fixture"
        className="absolute rounded-[6px] bg-accent font-semibold text-white"
        style={{ left: 610, top: 7, width: 78, height: 23, fontSize: 9.5 }}>
        + Add fixture
      </button>
      <div className="absolute rounded-[4px] font-semibold text-label"
        style={{ left: 9, top: 39, width: 679, height: 21, background: "#0f0f12", fontSize: 8.5 }}>
        {[["Name", 22], ["Type", 135], ["Address", 202], ["Class", 307], ["Groups", 367]].map(([t, x]) => (
          <span key={t} className="absolute" style={{ left: x as number, top: 5 }}>{t}</span>
        ))}
      </div>
      <div data-testid="fixture-rows" className="absolute overflow-y-auto" style={{ left: 9, top: 65, width: 679, height: 320 }}>
        {rows.map((row, i) => {
          const t = TYPE_STYLE[row.type];
          const dimmed = row.status !== "active";
          const one = row.members.length === 1 ? row.members[0] : null;
          return (
            <div key={row.key} data-testid={`row-${row.key}`} className="relative rounded-[5px]"
              style={{ height: 31, marginBottom: 5, background: i % 2 === 0 ? "#232329" : "transparent" }}>
              <StatusDot row={row} />
              <span className="absolute rounded-full" style={{ left: 24.5, top: 9.5, width: 12, height: 12,
                background: row.color ?? "#5a5a62" }} />
              <span className={`absolute truncate font-semibold ${dimmed ? "text-dim" : "text-fg"}`}
                style={{ left: 42, top: 7.5, width: 88, fontSize: 10.5 }}>{row.label}</span>
              <span className="absolute flex items-center justify-center rounded-[4px] font-bold"
                style={{ left: 135, top: 7, width: 39, height: 17, fontSize: 8, color: t.text,
                  border: `1.2px solid ${t.color}`, background: t.bg }}>{t.label}</span>
              <span className="absolute font-mono text-dim" style={{ left: 202, top: 9, fontSize: 9.5 }}>{row.address}</span>
              <span className="absolute flex items-center justify-center rounded-full font-bold"
                style={{ left: 307, top: 7.5, width: 39, height: 16, fontSize: 8.5,
                  background: row.klass === "fast" ? "#295240" : "#34343c",
                  color: row.klass === "fast" ? "var(--color-go)" : "var(--color-dim)" }}>{row.klass}</span>
              <span className="absolute truncate text-dim" style={{ left: 367, top: 9, width: 190, fontSize: 9 }}>
                {row.groups.join(" · ")}
              </span>
              {row.status === "active" && one && one.type !== "wled" && (
                <button onClick={() => void store.op("fixtures.test", { fixture_id: one.id })}
                  data-testid={`test-${one.id}`}
                  className="absolute rounded-[5px] border font-bold hover:border-dim"
                  style={{ left: 570, top: 5, width: 45, height: 21, fontSize: 9.5, borderColor: "#3e3e45", background: "#1f1f24" }}>
                  Test
                </button>
              )}
              {row.status === "not_added" && one?.ip && (
                <button onClick={() => add(one)} data-testid={`add-${one.id}`}
                  className="absolute rounded-[5px] border font-bold text-dim hover:text-fg"
                  style={{ left: 570, top: 5, width: 45, height: 21, fontSize: 9.5, borderColor: "#3e3e45" }}>
                  Add
                </button>
              )}
              {one && row.status !== "planned" && (
                <button aria-label={`Remove ${row.label}`} title="Remove from the rig"
                  onClick={() => void store.op("fixtures.remove", { fixture_id: one.id })}
                  className="absolute text-label hover:text-red" style={{ left: 652, top: 7, width: 16, height: 16, fontSize: 11 }}>
                  ×
                </button>
              )}
            </div>
          );
        })}
      </div>
      <div className="absolute text-label" style={{ left: 14, top: 387, fontSize: 8 }}>
        &apos;slow&apos; = LIFX (~100–250 ms, washes);&nbsp; &apos;fast&apos; = WLED / virtual (≤ 50 ms, flashes, chases)
      </div>
      {adding && <AddFixtureForm onClose={() => setAdding(false)} />}
    </Panel>
  );
}
