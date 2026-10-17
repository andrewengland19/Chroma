// Agent tab, bottom left: what was asked, the agent's answer, and each tool call it made.
import { useEffect, useState } from "react";
import { useSelect } from "../../store";
import { Panel } from "../ui";

function useElapsed(running: boolean) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    if (!running) return;
    const t0 = Date.now();
    setSecs(0);
    const id = setInterval(() => setSecs(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [running]);
  return secs;
}

export function ReplyPanel() {
  const agent = useSelect((s) => s.state?.agent);
  const running = agent?.status === "running";
  const secs = useElapsed(running);
  return (
    <Panel style={{ left: 19, top: 280, width: 560, height: 395 }}>
      <div className="label absolute" style={{ left: 12, top: 10 }}>Agent</div>
      <div data-testid="agent-reply" className="absolute overflow-y-auto" style={{ left: 12, top: 26, width: 536, height: 357, fontSize: 11, lineHeight: "16px" }}>
        {!agent || agent.status === "idle" ? (
          <div className="text-dim">Nothing asked yet. The agent reads your bulbs and scenes, then sets the look.</div>
        ) : (
          <>
            <div className="text-dim" style={{ marginBottom: 8 }}>
              <span className="label" style={{ marginRight: 6 }}>You</span>{agent.prompt}
            </div>
            {running && (
              <div data-testid="agent-running" className="flex items-center text-fg" style={{ gap: 8 }}>
                <span className="inline-block animate-spin rounded-full border-2 border-accent border-t-transparent" style={{ width: 11, height: 11 }} />
                Working… <span className="font-mono text-dim">{secs}s</span>
              </div>
            )}
            {agent.status === "error" && (
              <div data-testid="agent-error" className="text-red" style={{ userSelect: "text" }}>{agent.error}</div>
            )}
            {agent.status === "done" && (
              <div className="whitespace-pre-wrap text-fg" style={{ userSelect: "text" }}>{agent.reply || "Done."}</div>
            )}
            {agent.actions.length > 0 && (
              <div style={{ marginTop: 14 }}>
                <div className="label" style={{ marginBottom: 5 }}>What it did</div>
                <ul data-testid="agent-actions" className="flex flex-col" style={{ gap: 4 }}>
                  {agent.actions.map((a, i) => (
                    <li key={i} className="flex" style={{ gap: 8, fontSize: 10 }}>
                      <span className="shrink-0 font-mono text-label" style={{ width: 86 }}>{a.tool}</span>
                      <span className="text-dim">{a.summary}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
