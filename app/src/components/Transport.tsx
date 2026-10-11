import type { CSSProperties, ReactNode } from "react";
import { Chip } from "./ui";
import { useSelect } from "../store";

function Btn({ style, children, title, testId }: { style: CSSProperties; children: ReactNode; title: string; testId: string }) {
  // Rendered per the mockup; recording/playback arrive in M5, blackout in M4.
  return (
    <button aria-disabled="true" title={title} data-testid={testId}
      className="absolute flex items-center justify-center rounded-[6px] font-bold"
      style={{ top: 52, height: 29, fontSize: 9.5, letterSpacing: "0.01em", gap: 6, ...style }}>
      {children}
    </button>
  );
}

export function TransportButtons() {
  return (
    <>
      <Btn testId="btn-rec" title="Record (arrives in M5)" style={{ left: 791, width: 68, background: "var(--color-rec)", color: "#fff" }}>
        <span style={{ fontSize: 7 }}>●</span>REC
      </Btn>
      <Btn testId="btn-play" title="Play a take (arrives in M5)"
        style={{ left: 865, width: 67, background: "#222a28", border: "1.5px solid var(--color-go)", color: "var(--color-go)" }}>
        <svg width="8" height="9" viewBox="0 0 8 9"><path d="M0 0 L8 4.5 L0 9 Z" fill="currentColor" /></svg>PLAY
      </Btn>
      <Btn testId="btn-stop" title="Stop (arrives in M5)"
        style={{ left: 938, width: 68, background: "var(--color-ctl)", border: "1.5px solid var(--color-fg)", color: "var(--color-fg)" }}>
        <span style={{ width: 5, height: 5, background: "currentColor", display: "inline-block" }} />STOP
      </Btn>
      <Btn testId="btn-blackout" title="Blackout (arrives in M4)"
        style={{ left: 1013, width: 75, border: "1.5px solid var(--color-red)", color: "var(--color-red)" }}>
        BLACKOUT
      </Btn>
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
