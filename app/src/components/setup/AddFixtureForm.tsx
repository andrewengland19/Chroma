// Minimal "+ Add fixture" form (not in the mockups — flagged for review): a virtual light,
// or a LIFX bulb by IP address (Discover LIFX is the usual way to add bulbs).
import { useState } from "react";
import { useStore } from "../../store";

export function AddFixtureForm({ onClose }: { onClose: () => void }) {
  const store = useStore();
  const [type, setType] = useState<"virtual" | "lifx">("virtual");
  const [label, setLabel] = useState("");
  const [ip, setIp] = useState("");
  const [error, setError] = useState("");
  const save = async () => {
    const fixture = type === "virtual"
      ? { label, type, status: "active", class: "fast", x: 0.5, y: 0.5 }
      : { label, type, status: "active", class: "slow", x: 0.5, y: 0.5, ip };
    const r = await store.ipc.request({ op: "fixtures.upsert", fixture });
    if (r.ok) onClose();
    else setError(r.error || "could not add the fixture");
  };
  const field = "rounded-[5px] border border-ctl-line bg-ctl px-2 text-fg outline-none focus:border-accent";
  return (
    <div role="dialog" aria-label="Add fixture" data-testid="add-form"
      className="absolute flex flex-col rounded-[8px] border border-line bg-panel shadow-xl"
      style={{ right: 9, top: 34, width: 260, padding: 12, gap: 8, fontSize: 10, zIndex: 10 }}>
      <div className="font-semibold" style={{ fontSize: 11 }}>Add fixture</div>
      <div className="flex" style={{ gap: 6 }}>
        {(["virtual", "lifx"] as const).map((t) => (
          <button key={t} onClick={() => setType(t)} aria-pressed={type === t}
            className={`flex-1 rounded-[5px] border font-semibold ${type === t ? "border-accent text-fg" : "border-ctl-line text-dim"}`}
            style={{ height: 22 }}>{t === "virtual" ? "Virtual light" : "LIFX by IP"}</button>
        ))}
      </div>
      <input aria-label="Name" placeholder="Name" value={label} onChange={(e) => setLabel(e.target.value)} className={field} style={{ height: 24 }} />
      {type === "lifx" && (
        <input aria-label="IP address" placeholder="192.168.0.40" value={ip} onChange={(e) => setIp(e.target.value)}
          className={`${field} font-mono`} style={{ height: 24 }} />
      )}
      {error && <div className="text-red" style={{ fontSize: 9 }}>{error}</div>}
      <div className="flex justify-end" style={{ gap: 6 }}>
        <button onClick={onClose} className="rounded-[5px] border border-ctl-line text-dim" style={{ height: 22, width: 60 }}>Cancel</button>
        <button onClick={save} disabled={!label.trim() || (type === "lifx" && !ip.trim())}
          className="rounded-[5px] bg-accent font-semibold text-white disabled:opacity-40" style={{ height: 22, width: 60 }}>Add</button>
      </div>
    </div>
  );
}
