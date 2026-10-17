// Console bottom strip (gui-1): what is being recorded or played, and one row of event
// bars per group across the song, with the playhead. Data: State.rec / play / activity.
import { Panel } from "../ui";
import { hms } from "../../lib/takes";
import { useSelect, useSongPosition } from "../../store";

const BOX = { left: 216.5, top: 9.5, width: 893.5, height: 38.5 };
const PAD = 6;
const ROWS = 4;

export function ActivityStrip() {
  const rec = useSelect((s) => s.state?.rec);
  const play = useSelect((s) => s.state?.play);
  const act = useSelect((s) => s.state?.activity);
  const takes = useSelect((s) => s.takes);
  const pos = useSongPosition() ?? 0;
  const recording = rec?.state === "recording";
  const armed = rec?.state === "armed";
  const playing = play?.state === "playing";
  const dur = act?.dur_ms || 1;
  const innerW = BOX.width - 2 * PAD;
  const x = (p: number) => PAD + Math.min(1, Math.max(0, p / dur)) * innerW;
  const playName = takes?.find((t) => t.id === play?.take_id)?.name ?? "";
  let dot = "var(--color-label)";
  let title = "No take running";
  let sub = "REC records this song · PLAY replays its newest take";
  if (recording) {
    dot = "var(--color-rec)";
    title = `REC  ${rec!.name}`;
    sub = `${hms(rec!.elapsed_ms)}  ·  ${rec!.events} events  ·  ${rec!.groups_touched} groups touched`;
  } else if (armed) {
    dot = "var(--color-rec)";
    title = `ARMED  ${rec!.name || "new take"}`;
    sub = "recording starts when the song plays";
  } else if (playing) {
    dot = "var(--color-go)";
    title = `PLAY  ${playName}`;
    sub = `${hms(pos)}${play!.loop ? "  ·  loop" : ""}`;
  }
  return (
    <Panel style={{ left: 19, top: 618, width: 1140, height: 57 }}>
      <div data-testid="activity-strip" className="absolute inset-0">
        <span className={`absolute rounded-full ${armed ? "animate-pulse" : ""}`}
          style={{ left: 13.5, top: 22.5, width: 11, height: 11, background: dot }} />
        <div className="absolute truncate font-bold text-fg" data-testid="activity-title"
          style={{ left: 31, top: 13.5, width: 175, fontSize: 9, lineHeight: "14px", whiteSpace: "pre" }}>{title}</div>
        <div className="absolute truncate text-dim" data-testid="activity-sub"
          style={{ left: 31, top: 29.5, width: 180, fontSize: 7.3, lineHeight: "12px", whiteSpace: "pre" }}>{sub}</div>
        <div className="absolute overflow-hidden rounded-[5px] border border-line bg-well" style={BOX}>
          {(act?.lanes ?? []).slice(0, ROWS).map((lane, r) =>
            lane.spans.map(([p0, p1], k) => (
              <span key={`${lane.group_id}-${k}`} className="absolute rounded-[1.5px]"
                style={{ left: x(p0), width: Math.max(2, x(p1) - x(p0)), top: 4.5 + r * 8, height: 4, background: lane.color, opacity: 0.85 }} />
            )))}
          {(recording || playing) && (
            <span data-testid="activity-playhead" className="absolute"
              style={{ left: x(pos), top: 0, bottom: 0, width: 1, background: "var(--color-rec)" }} />
          )}
        </div>
      </div>
    </Panel>
  );
}
