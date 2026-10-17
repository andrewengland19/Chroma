// Agent tab, top left: describe the room in plain words → agent.ask (⌘↩), limited
// to the checked Spaces (none checked = Living Room only, the engine's default).
import { useState } from "react";
import { useSelect, useStore } from "../../store";
import { Panel } from "../ui";

const EXAMPLES = [
  "Rainy evening, cozy but I need to read",
  "Like vibes1 but cooler and dimmer near the TV",
  "Movie night: TV wall deep teal, everything else nearly off",
];

export function PromptPanel() {
  const store = useStore();
  const agent = useSelect((s) => s.state?.agent);
  const connected = useSelect((s) => !!s.status?.connected);
  const spaces = useSelect((s) => s.rig?.spaces) ?? [];
  const [draft, setDraft] = useState("");
  const [checked, setChecked] = useState<string[]>([]);
  const picked = spaces.filter((s) => checked.includes(s.id)).map((s) => s.id);
  const all = spaces.length > 0 && picked.length === spaces.length;
  const toggle = (id: string) => setChecked((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));
  const running = agent?.status === "running";
  const why = !connected || !agent ? "Engine not connected" : agent.blocked ? agent.blocked : running ? "Working on it…" : "";
  const canAsk = !why && !!draft.trim() && draft.trim().length <= 500;
  const ask = async () => {
    if (!canAsk) return;
    const r = await store.op("agent.ask", { prompt: draft.trim(), spaces: picked });
    if (r.ok) setDraft("");
  };
  return (
    <Panel style={{ left: 19, top: 45, width: 560, height: 220 }}>
      <div className="label absolute" style={{ left: 12, top: 10 }}>Describe the room</div>
      <textarea data-testid="agent-prompt" aria-label="Describe the room" value={draft} maxLength={500}
        placeholder="How should the room feel?"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            void ask();
          }
        }}
        className="absolute resize-none rounded-[6px] border border-ctl-line bg-ctl text-fg outline-none focus:border-accent"
        style={{ left: 12, top: 26, width: 536, height: 78, padding: "8px 10px", fontSize: 12, lineHeight: "17px", userSelect: "text" }} />
      <div className="absolute flex flex-wrap" style={{ left: 12, top: 112, width: 536, gap: 6 }}>
        {EXAMPLES.map((x) => (
          <button key={x} onClick={() => setDraft(x)}
            className="rounded-full border border-ctl-line text-dim hover:border-accent hover:text-fg"
            style={{ height: 20, padding: "0 9px", fontSize: 9 }}>
            {x}
          </button>
        ))}
      </div>
      <div role="group" aria-label="Spaces" data-testid="agent-spaces" className="absolute flex items-center text-dim"
        style={{ left: 12, top: 166, width: 536, gap: 12, fontSize: 10 }}>
        <span className="label">Spaces</span>
        {spaces.length > 0 && (
          <label className="flex items-center" style={{ gap: 4 }}>
            <input type="checkbox" checked={all} onChange={() => setChecked(all ? [] : spaces.map((s) => s.id))}
              className="accent-[var(--color-accent)]" />
            <span className={all ? "text-fg" : ""}>All</span>
          </label>
        )}
        {spaces.map((s) => (
          <label key={s.id} className="flex items-center" style={{ gap: 4 }}>
            <input type="checkbox" checked={picked.includes(s.id)} onChange={() => toggle(s.id)}
              className="accent-[var(--color-accent)]" />
            <span className={picked.includes(s.id) ? "text-fg" : ""}>{s.name}</span>
          </label>
        ))}
        {picked.length === 0 && (
          <span style={{ fontSize: 9 }}>
            — none checked: {spaces.find((s) => s.id === "living-room")?.name ?? "Living Room"} only
          </span>
        )}
      </div>
      <div data-testid="agent-why" className={`absolute ${agent?.blocked ? "text-warn" : "text-dim"}`}
        style={{ left: 12, bottom: 16, width: 420, fontSize: 9.5 }}>
        {why || "Lights change as soon as the agent answers. Undo puts them back."}
      </div>
      <button data-testid="agent-ask" onClick={() => void ask()} disabled={!canAsk}
        className="absolute rounded-[6px] bg-accent font-semibold text-white disabled:opacity-40"
        style={{ right: 12, bottom: 10, width: 96, height: 26, fontSize: 11 }}>
        {running ? "Working…" : "Ask  ⌘↩"}
      </button>
    </Panel>
  );
}
