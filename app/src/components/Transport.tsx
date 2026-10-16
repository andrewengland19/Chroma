import type { CSSProperties, ReactNode } from "react";
import { BlackoutButton } from "./console/BankPanel";
import { Chip } from "./ui";
import { takeForTrack } from "../lib/takes";
import { useSelect, useStore } from "../store";

function Btn({ style, children, title, testId, onClick, pressed }: {
  style: CSSProperties; children: ReactNode; title: string; testId: string; onClick: () => void; pressed?: boolean;
}) {
  return (
    <button title={title} data-testid={testId} onClick={onClick} aria-pressed={pressed}
      className="absolute flex items-center justify-center rounded-[6px] font-bold"
      style={{ height: 29, fontSize: 9.5, letterSpacing: "0.01em", gap: 6, ...style }}>
      {children}
    </button>
  );
}

/** REC / PLAY / STOP. REC lit = recording (outlined = idle, dimmed fill = armed); PLAY lit =
 *  a take is playing. `takeId`: the take the buttons act on (Takes screen); without it REC
 *  records a new take and PLAY plays the newest take of the song now playing (Console). */
export function RecPlayStop({ left, top, takeId, mode, loop }: {
  left: number; top: number; takeId?: string | null; mode?: "replace" | "merge" | "overdub"; loop?: boolean;
}) {
  const store = useStore();
  const rec = useSelect((s) => s.state?.rec);
  const play = useSelect((s) => s.state?.play);
  const now = useSelect((s) => s.state?.now);
  const takes = useSelect((s) => s.takes);
  const recording = rec?.state === "recording";
  const armed = rec?.state === "armed";
  const playing = play?.state === "playing";
  const onRec = () => {
    if (recording || armed) return void store.op("rec.stop");
    const args: Record<string, unknown> = {};
    if (takeId) Object.assign(args, { take_id: takeId, mode: mode ?? "merge" });
    void store.op("rec.arm", args);
  };
  const onPlay = () => {
    const id = takeId ?? takeForTrack(takes ?? [], now)?.id;
    if (!id) return store.toast("warn", now?.title ? `No take of “${now.title}” yet: press REC to record one` : "Nothing is playing");
    void store.op("play.start", loop == null ? { take_id: id } : { take_id: id, loop });
  };
  const onStop = () => {
    if (recording || armed) void store.op("rec.stop");
    if (playing) void store.op("play.stop");
  };
  const recStyle: CSSProperties = recording
    ? { background: "var(--color-rec)", color: "#fff", border: "1.5px solid var(--color-rec)" }
    : { background: armed ? "#5a2930" : "#2b1d21", color: "var(--color-rec)", border: "1.5px solid var(--color-rec)" };
  const playStyle: CSSProperties = playing
    ? { background: "var(--color-go)", color: "#0d1f15", border: "1.5px solid var(--color-go)" }
    : { background: "#222a28", color: "var(--color-go)", border: "1.5px solid var(--color-go)" };
  return (
    <>
      <Btn testId="btn-rec" pressed={recording} onClick={onRec}
        title={recording ? "Stop recording (saves the take)" : armed ? "Armed: recording starts with the song — click to disarm"
          : takeId ? `Record into this take (${(mode ?? "merge").toUpperCase()})` : "Record a new take of this song"}
        style={{ left, top, width: 68, ...recStyle }}>
        <span className={armed ? "animate-pulse" : ""} style={{ fontSize: 7 }}>●</span>REC
      </Btn>
      <Btn testId="btn-play" pressed={playing} onClick={onPlay}
        title={takeId ? "Play this take with its song" : "Play the newest take of the song now playing"}
        style={{ left: left + 74, top, width: 67, ...playStyle }}>
        <svg width="8" height="9" viewBox="0 0 8 9"><path d="M0 0 L8 4.5 L0 9 Z" fill="currentColor" /></svg>PLAY
      </Btn>
      <Btn testId="btn-stop" onClick={onStop} title="Stop recording / playback"
        style={{ left: left + 147, top, width: 68, background: "var(--color-ctl)", border: "1.5px solid var(--color-fg)", color: "var(--color-fg)" }}>
        <span style={{ width: 5, height: 5, background: "currentColor", display: "inline-block" }} />STOP
      </Btn>
    </>
  );
}

export function TransportButtons() {
  const blackout = useSelect((s) => s.state?.blackout) ?? false;
  return (
    <>
      <RecPlayStop left={791} top={52} />
      <BlackoutButton testId="btn-blackout" on={blackout}
        style={{ left: 1013, top: 52, width: 75, height: 29, fontSize: 9.5, borderRadius: 6 }} />
    </>
  );
}

const BOARD: Record<string, { color: string; text: string }> = {
  none: { color: "var(--color-label)", text: "Board: none" },
  virtual: { color: "var(--color-warn)", text: "Board: virtual" },
  online: { color: "var(--color-go)", text: "Board: online" },
  offline: { color: "var(--color-red)", text: "Board: offline" },
};

export function StatusChips() {
  const h = useSelect((s) => s.state?.health);
  const atvUp = h?.atv === "up";
  const lifx = h?.lifx ?? { found: 0, total: 0 };
  const lifxColor = lifx.total > 0 && lifx.found === lifx.total ? "var(--color-go)"
    : lifx.found > 0 ? "var(--color-warn)" : "var(--color-red)";
  const board = BOARD[h?.board ?? "none"];
  return (
    <div className="absolute flex" style={{ left: 791, top: 86, gap: 8 }}>
      <Chip testId="chip-atv" width={69} color={atvUp ? "var(--color-go)" : "var(--color-red)"}>Apple TV</Chip>
      <Chip testId="chip-lifx" width={69} color={lifxColor}>{`LIFX ${lifx.found}/${lifx.total}`}</Chip>
      <Chip testId="chip-board" width={87} color={board.color}>{board.text}</Chip>
    </div>
  );
}
