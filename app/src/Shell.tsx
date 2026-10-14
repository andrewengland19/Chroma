// The window: a 1178 x 691 design canvas (the mockups' logical size) scaled to fit,
// tab bar, toasts and the engine-connection notice.
import { useEffect, useState } from "react";
import { ConsoleScreen } from "./screens/ConsoleScreen";
import { PendingScreen } from "./screens/PendingScreen";
import { SetupScreen } from "./screens/SetupScreen";
import { useSelect, useStore } from "./store";

export const CANVAS_W = 1178;
export const CANVAS_H = 691;
const TABS = ["Console", "Takes", "Setup"] as const;
type Tab = (typeof TABS)[number];

function useFitScale() {
  const calc = () => Math.min(window.innerWidth / CANVAS_W, window.innerHeight / CANVAS_H) || 1;
  const [scale, setScale] = useState(calc);
  useEffect(() => {
    const on = () => setScale(calc());
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return scale;
}

function EngineNotice() {
  const status = useSelect((s) => s.status);
  if (!status) return null;
  const text = status.connected
    ? status.fake ? "fake devices" : ""
    : status.phase === "starting" ? "starting engine…"
    : status.phase === "restarting" ? "engine restarting…"
    : status.phase === "waiting" ? "engine not running" : "connecting to engine…";
  if (!text) return null;
  return (
    <div data-testid="engine-notice" title={status.message}
      className={`absolute flex items-center ${status.connected ? "text-label" : "text-warn"}`}
      style={{ right: 19, top: 14, fontSize: 9, gap: 5 }}>
      <span className="rounded-full" style={{ width: 5, height: 5, background: status.connected ? "var(--color-label)" : "var(--color-warn)" }} />
      {text}
    </div>
  );
}

function Toasts() {
  const toasts = useSelect((s) => s.toasts);
  return (
    <div className="absolute flex flex-col items-end" style={{ right: 19, bottom: 24, gap: 6 }}>
      {toasts.map((t) => (
        <div key={t.id} role="status" className="rounded-[6px] border bg-panel"
          style={{ fontSize: 10, padding: "6px 10px",
            borderColor: t.level === "error" ? "var(--color-red)" : t.level === "warn" ? "var(--color-warn)" : "var(--color-line)" }}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

export function Shell() {
  const store = useStore();
  const scale = useFitScale();
  const [tab, setTab] = useState<Tab>(() => {
    const t = new URLSearchParams(window.location.search).get("tab") ?? "";
    return TABS.find((x) => x.toLowerCase() === t.toLowerCase()) ?? "Console";
  });
  useEffect(() => store.ipc.setTitle?.(`CHROMA — ${tab}`), [tab, store]);
  return (
    <div className="flex h-full w-full items-center justify-center bg-bg">
      <div data-testid="canvas" className="relative shrink-0"
        style={{ width: CANVAS_W, height: CANVAS_H, zoom: scale } as React.CSSProperties}>
        <nav className="absolute flex" style={{ left: 19, top: 9, gap: 6 }}>
          {TABS.map((t) => (
            <button key={t} onClick={() => setTab(t)} aria-current={tab === t ? "page" : undefined}
              className={`rounded-[6px] font-semibold ${tab === t ? "bg-accent text-white" : "text-dim hover:text-fg"}`}
              style={{ width: 72, height: 24, fontSize: 11 }}>
              {t}
            </button>
          ))}
        </nav>
        <EngineNotice />
        {tab === "Console" && <ConsoleScreen />}
        {tab === "Takes" && <PendingScreen name="Takes" milestone="M5" />}
        {tab === "Setup" && <SetupScreen />}
        <Toasts />
      </div>
    </div>
  );
}
