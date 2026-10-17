// Agent tab, bottom right: Undo the last run, or keep the look as the lite scene agent.<name>.
import { useEffect, useState } from "react";
import { useSelect, useStore } from "../../store";
import { Panel } from "../ui";

const NAME_RE = /^[a-z0-9_-]{1,32}$/;

export function ActionsPanel() {
  const store = useStore();
  const agent = useSelect((s) => s.state?.agent);
  const who = useSelect((s) => s.state?.who);
  const [name, setName] = useState("");
  const suggested = agent?.suggested_name ?? null;
  useEffect(() => {
    if (suggested) setName(suggested);
  }, [suggested]);
  const idle = !!agent && !agent.blocked && agent.status !== "running";
  const canSave = idle && agent.status === "done" && NAME_RE.test(name);
  const field = "rounded-[5px] border border-ctl-line bg-ctl text-fg outline-none focus:border-accent";
  return (
    <Panel style={{ left: 594, top: 530, width: 565, height: 145 }}>
      <div className="label absolute" style={{ left: 12, top: 10 }}>Keep it</div>
      <button data-testid="agent-undo" onClick={() => void store.op("agent.undo")} disabled={!idle || !agent.can_undo}
        className="absolute rounded-[6px] border border-ctl-line font-semibold text-fg hover:border-accent disabled:opacity-40"
        style={{ left: 12, top: 28, width: 96, height: 26, fontSize: 11 }}>
        Undo
      </button>
      <div className="absolute flex items-center" style={{ left: 124, top: 28, gap: 6 }}>
        <span className="font-mono text-dim" style={{ fontSize: 11 }}>agent.</span>
        <input data-testid="agent-name" aria-label="Scene name" value={name} maxLength={32} placeholder="scene_name"
          onChange={(e) => setName(e.target.value.toLowerCase())}
          className={`${field} font-mono`} style={{ width: 210, height: 26, padding: "0 8px", fontSize: 11, userSelect: "text" }} />
        <button data-testid="agent-save" onClick={() => void store.op("agent.save", { name })} disabled={!canSave}
          className="rounded-[6px] bg-accent font-semibold text-white disabled:opacity-40" style={{ width: 96, height: 26, fontSize: 11 }}>
          Save scene
        </button>
      </div>
      <div className="absolute flex flex-col text-dim" style={{ left: 12, top: 66, width: 541, gap: 4, fontSize: 9.5 }}>
        {agent?.saved_as && (
          <div data-testid="agent-saved" className="text-go">
            Saved — recall it any time with <span className="font-mono">lite scene {agent.saved_as}</span>
          </div>
        )}
        {name && !NAME_RE.test(name) && <div className="text-warn">Names use a–z, 0–9, _ and - (up to 32).</div>}
        {who === "board" && agent && agent.status !== "idle" && (
          <div>WHO DRIVES is on BOARD, so album art won't paint over the look. Switch to AUTO on the Console to hand the lights back.</div>
        )}
      </div>
    </Panel>
  );
}
