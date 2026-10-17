// Spaces (rooms) setup, opened by double-clicking the Console's Room panel: add,
// rename and remove Spaces, and say which Space each light is in. Every change is
// an op (spaces.upsert / spaces.remove / fixtures.upsert); the rig refetch shows it.
import { useEffect, useState } from "react";
import { useSelect, useStore } from "../store";
import { DEFAULT_SPACE } from "./RoomView";

const field = "rounded-[5px] border border-ctl-line bg-ctl text-fg outline-none focus:border-accent";

function SpaceName({ id, name }: { id: string; name: string }) {
  const store = useStore();
  const [draft, setDraft] = useState(name);
  useEffect(() => setDraft(name), [name]);
  const commit = async () => {
    const v = draft.trim();
    if (!v || v === name) return setDraft(name);
    const r = await store.op("spaces.upsert", { space: { id, name: v } });
    if (!r.ok) setDraft(name);
  };
  return (
    <input aria-label={`Name of ${name}`} value={draft} maxLength={24}
      onChange={(e) => setDraft(e.target.value)} onBlur={() => void commit()}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      className={`${field} min-w-0 flex-1`} style={{ height: 24, padding: "0 8px", fontSize: 11, userSelect: "text" }} />
  );
}

export function SpacesModal({ onClose }: { onClose: () => void }) {
  const store = useStore();
  const rig = useSelect((s) => s.rig);
  const [adding, setAdding] = useState("");
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
  const spaces = rig?.spaces ?? [];
  const lights = (rig?.fixtures ?? []).filter((f) => f.status !== "planned");
  const add = async () => {
    if (!adding.trim()) return;
    const r = await store.op("spaces.upsert", { space: { name: adding.trim() } });
    if (r.ok) setAdding("");
  };
  return (
    <div className="absolute inset-0 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.55)", zIndex: 20 }}
      onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-label="Spaces" data-testid="spaces-modal"
        className="relative flex flex-col rounded-[10px] border border-line bg-panel shadow-xl"
        style={{ width: 600, maxHeight: 520, padding: 16, gap: 12 }}>
        <div className="flex items-center">
          <div className="font-semibold" style={{ fontSize: 13 }}>Spaces</div>
          <div className="text-dim" style={{ fontSize: 9.5, marginLeft: 10 }}>
            Rooms you can show on the board and point the agent at. Each light lives in one.
          </div>
          <button aria-label="Close" onClick={onClose} className="ml-auto text-dim hover:text-fg" style={{ fontSize: 14, width: 22 }}>✕</button>
        </div>
        <div className="flex min-h-0" style={{ gap: 16 }}>
          <div className="flex flex-col" style={{ width: 230, gap: 6 }}>
            <div className="label">Spaces</div>
            {spaces.map((s) => {
              const n = lights.filter((f) => (f.space ?? DEFAULT_SPACE) === s.id).length;
              return (
                <div key={s.id} data-testid={`space-${s.id}`} className="flex items-center" style={{ gap: 6 }}>
                  <SpaceName id={s.id} name={s.name} />
                  <span className="text-dim" style={{ fontSize: 9, width: 18, textAlign: "right" }}>{n}</span>
                  {s.id === DEFAULT_SPACE ? (
                    <span className="text-dim" title="The default space can be renamed, not removed" style={{ fontSize: 8.5, width: 44 }}>default</span>
                  ) : (
                    <button aria-label={`Remove ${s.name}`} onClick={() => void store.op("spaces.remove", { space_id: s.id })}
                      className="rounded-[5px] border border-ctl-line text-dim hover:border-red hover:text-fg"
                      style={{ width: 44, height: 22, fontSize: 9 }}>Remove</button>
                  )}
                </div>
              );
            })}
            <div className="flex items-center" style={{ gap: 6, marginTop: 4 }}>
              <input aria-label="New space name" placeholder="New space, e.g. Kitchen" value={adding} maxLength={24}
                onChange={(e) => setAdding(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void add()}
                className={`${field} min-w-0 flex-1`} style={{ height: 24, padding: "0 8px", fontSize: 11, userSelect: "text" }} />
              <button onClick={() => void add()} disabled={!adding.trim()}
                className="rounded-[6px] bg-accent font-semibold text-white disabled:opacity-40" style={{ width: 70, height: 24, fontSize: 10 }}>
                + Add
              </button>
            </div>
          </div>
          <div className="flex min-h-0 flex-1 flex-col" style={{ gap: 6 }}>
            <div className="label">Lights</div>
            <div className="flex flex-col overflow-y-auto" style={{ gap: 4, maxHeight: 420 }}>
              {lights.map((f) => (
                <label key={f.id} className="flex items-center" style={{ gap: 8, fontSize: 10.5 }}>
                  <span className="rounded-full" style={{ width: 7, height: 7, background: f.color ?? "#3a3a42", flexShrink: 0 }} />
                  <span className={`min-w-0 flex-1 truncate ${f.status === "active" ? "text-fg" : "text-dim"}`}
                    title={f.status === "active" ? f.label : `${f.label} — not in the rig yet`}>{f.label}</span>
                  <select aria-label={`Space for ${f.label}`} value={f.space ?? DEFAULT_SPACE}
                    onChange={(e) => void store.op("fixtures.upsert", { fixture: { ...f, space: e.target.value } })}
                    className={field} style={{ width: 140, height: 22, fontSize: 10, padding: "0 4px" }}>
                    {spaces.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </label>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
