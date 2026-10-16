// Take details (gui-2 bottom): name (double-click to rename), song, stats, training flag,
// export / duplicate / delete.
import { useState } from "react";
import { Panel } from "../ui";
import { mmss } from "../../lib/format";
import { longDate } from "../../lib/takes";
import type { TakeHeader } from "../../contracts";
import { useStore } from "../../store";

export function Details({ header, onSelect }: { header: TakeHeader | null; onSelect: (id: string | null) => void }) {
  const store = useStore();
  const [editing, setEditing] = useState(false);
  if (!header) {
    return <Panel style={{ left: 282, top: 543, width: 877, height: 131 }}><div className="label absolute" style={{ left: 12, top: 10 }}>Take details</div></Panel>;
  }
  const st = header.stats;
  const stat = (x: number, value: string, label: string, testId: string) => (
    <div className="absolute" style={{ left: x - 282, top: 75 }}>
      <div data-testid={testId} className="font-mono font-bold text-fg" style={{ fontSize: 14, lineHeight: "18px" }}>{value}</div>
      <div className="text-label" style={{ fontSize: 7.5, lineHeight: "12px" }}>{label}</div>
    </div>
  );
  const exportTake = async () => {
    const r = await store.op("takes.export", { take_id: header.id });
    if (!r.ok) return;
    const filename = String(r.filename);
    const text = String(r.jsonl);
    if (store.ipc.saveText) {
      try {
        const path = await store.ipc.saveText(filename, text);
        store.toast("info", `Exported to ${path}`);
      } catch (e) {
        store.toast("error", `Export failed: ${String(e)}`);
      }
      return;
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "application/x-ndjson" }));
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const duplicate = async () => {
    const r = await store.op("takes.duplicate", { take_id: header.id });
    if (r.ok && r.take_id) onSelect(String(r.take_id));
  };
  const del = async () => {
    if (!window.confirm(`Delete “${header.name}”? (It is moved to takes/.deleted)`)) return;
    const r = await store.op("takes.delete", { take_id: header.id });
    if (r.ok) onSelect(null);
  };
  const btn = "absolute flex items-center justify-center rounded-[6px] font-bold";
  return (
    <Panel style={{ left: 282, top: 543, width: 877, height: 131 }}>
      <div className="label absolute" style={{ left: 12, top: 10 }}>Take details</div>
      {editing ? (
        <input data-testid="take-name-input" autoFocus defaultValue={header.name} maxLength={64}
          onKeyDown={(e) => {
            const v = (e.target as HTMLInputElement).value.trim();
            if (e.key === "Enter") {
              if (v && v !== header.name) void store.op("takes.rename", { take_id: header.id, name: v });
              setEditing(false);
            }
            if (e.key === "Escape") setEditing(false);
          }}
          onBlur={() => setEditing(false)}
          className="absolute rounded-[4px] border border-accent bg-ctl font-bold text-fg outline-none"
          style={{ left: 14, top: 29, width: 400, height: 22, fontSize: 14, paddingLeft: 3 }} />
      ) : (
        <div data-testid="take-name" title="Double-click to rename" onDoubleClick={() => setEditing(true)}
          className="absolute truncate font-bold text-fg" style={{ left: 17, top: 30, width: 560, fontSize: 15, lineHeight: "20px" }}>
          {header.name}
        </div>
      )}
      <div className="absolute truncate text-dim" style={{ left: 17, top: 52, width: 560, fontSize: 8.5, lineHeight: "12px" }}>
        {[header.artist, header.album, header.created_ms ? `recorded ${longDate(header.created_ms)}` : ""].filter(Boolean).join(" · ")}
      </div>
      {stat(299, String(st?.events ?? 0), "events", "stat-events")}
      {stat(411, String(st?.groups_used.length ?? 0), "groups used", "stat-groups")}
      {stat(523, mmss(st?.length_ms ?? header.dur_ms), "length", "stat-length")}
      {stat(635, header.sync_err_ms == null ? "—" : `±${Math.round(header.sync_err_ms)} ms`, "sync error", "stat-sync")}
      {stat(748, header.performer || "—", "performed by", "stat-performer")}
      <div className="label absolute" style={{ left: 592, top: 37 }}>Include in AI training set</div>
      <button data-testid="training" role="switch" aria-checked={header.training}
        onClick={() => void store.op("takes.set_flag", { take_id: header.id, flag: "training", value: !header.training })}
        className="absolute rounded-full" style={{ left: 592, top: 52, width: 34, height: 17,
          background: header.training ? "var(--color-go)" : "var(--color-ctl-line)" }}>
        <span className="absolute rounded-full bg-white" style={{ top: 2, left: header.training ? 19 : 2, width: 13, height: 13 }} />
      </button>
      <span className="absolute text-fg" style={{ left: 634, top: 55, fontSize: 8.5 }}>{header.training ? "on" : "off"}</span>
      <button data-testid="take-export" onClick={() => void exportTake()} className={`${btn} border border-line bg-ctl text-fg`}
        style={{ left: 592, top: 85, width: 87, height: 28, fontSize: 9.5 }}>Export .jsonl</button>
      <button data-testid="take-duplicate" onClick={() => void duplicate()} className={`${btn} border border-line bg-ctl text-fg`}
        style={{ left: 685, top: 85, width: 87, height: 28, fontSize: 9.5 }}>Duplicate</button>
      <button data-testid="take-delete" onClick={() => void del()} className={btn}
        style={{ left: 778, top: 85, width: 87, height: 28, fontSize: 9.5, border: "1.2px solid var(--color-red)", color: "var(--color-red)", background: "#2a1d21" }}>
        Delete
      </button>
    </Panel>
  );
}
