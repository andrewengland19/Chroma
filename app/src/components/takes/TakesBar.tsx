// Takes transport bar (gui-2 top): REC / PLAY / STOP, record mode, sync nudge, loop, time.
import { Panel } from "../ui";
import { RecPlayStop } from "../Transport";
import { clockMs, clockS, nudgeText } from "../../lib/takes";
import type { TakeHeader } from "../../contracts";
import { useSelect, useStore } from "../../store";

export type RecMode = "replace" | "merge" | "overdub";
const MODES: { v: RecMode; label: string }[] = [
  { v: "replace", label: "REPLACE" }, { v: "merge", label: "MERGE" }, { v: "overdub", label: "OVERDUB" }];

export function TakesBar({ header, mode, setMode, loop, setLoop, pos }: {
  header: TakeHeader | null; mode: RecMode; setMode: (m: RecMode) => void; loop: boolean; setLoop: (l: boolean) => void;
  pos: number;
}) {
  const store = useStore();
  const play = useSelect((s) => s.state?.play);
  const playingThis = play?.state === "playing" && play.take_id === header?.id;
  const nudge = header?.nudge_ms ?? 0;
  const setNudge = (ms: number) => {
    if (!header) return;
    void store.op("take.nudge", { take_id: header.id, ms: Math.max(-2000, Math.min(2000, ms)) });
  };
  const onLoop = () => {
    const next = !loop;
    setLoop(next);
    if (playingThis && header) void store.op("play.start", { take_id: header.id, loop: next });
  };
  const loopOn = playingThis ? !!play?.loop : loop;
  return (
    <Panel style={{ left: 282, top: 45, width: 877, height: 50 }}>
      <div className="absolute" style={{ left: -282, top: -45 }}>
        <RecPlayStop left={293} top={55} takeId={header?.id ?? null} mode={mode} loop={loop} stopBorder="var(--color-ctl-line)" />
      </div>
      <div role="radiogroup" aria-label="Record mode" data-testid="rec-mode"
        className="absolute flex rounded-[6px] border border-ctl-line bg-ctl"
        style={{ left: 255, top: 14, width: 207, height: 22, padding: 2 }}>
        {MODES.map((m) => (
          <button key={m.v} role="radio" aria-checked={mode === m.v} onClick={() => setMode(m.v)}
            title={m.v === "replace" ? "New events replace the whole take"
              : m.v === "merge" ? "Add new events, keep everything (old take not played)"
              : "Old take plays; channels you touch are punched in (750 ms)"}
            className={`flex-1 rounded-[4.5px] font-bold ${mode === m.v ? "bg-accent text-white" : "text-dim hover:text-fg"}`}
            style={{ fontSize: 9.5, letterSpacing: "0.02em" }}>{m.label}</button>
        ))}
      </div>
      <div className="label absolute" style={{ left: 479, top: 3 }}>Sync nudge</div>
      <div className="absolute flex items-center rounded-[6px] border border-ctl-line bg-ctl"
        style={{ left: 479, top: 14, width: 113, height: 22 }}>
        <button data-testid="nudge-minus" aria-label="Nudge earlier" title="Earlier (shift: 1 ms)" disabled={!header}
          onClick={(e) => setNudge(nudge - (e.shiftKey ? 1 : 5))} className="text-dim hover:text-fg"
          style={{ width: 28, fontSize: 12 }}>−</button>
        <span data-testid="nudge" className="flex-1 text-center font-mono text-fg" style={{ fontSize: 9.5 }}
          title="Positive = the take plays later">{nudgeText(nudge)}</span>
        <button data-testid="nudge-plus" aria-label="Nudge later" title="Later (shift: 1 ms)" disabled={!header}
          onClick={(e) => setNudge(nudge + (e.shiftKey ? 1 : 5))} className="text-dim hover:text-fg"
          style={{ width: 28, fontSize: 12 }}>+</button>
      </div>
      <button data-testid="loop" aria-pressed={loopOn} onClick={onLoop}
        title="Loop: start the take again when its song repeats"
        className={`absolute font-bold ${loopOn ? "text-accent" : "text-dim hover:text-fg"}`}
        style={{ left: 614, top: 18, fontSize: 9.5, letterSpacing: "0.02em" }}>
        LOOP <span style={{ fontSize: 6.5, verticalAlign: "1px" }}>{loopOn ? "●" : "○"}</span>
      </button>
      <div data-testid="take-time" className="absolute text-right font-mono text-fg"
        style={{ right: 15, top: 18, fontSize: 10, whiteSpace: "pre" }}>
        {`${clockMs(pos)} / ${clockS(header?.dur_ms)}`}
      </div>
    </Panel>
  );
}
