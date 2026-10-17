// Scenes picker, opened by double-clicking the Console's Cues pane in Scenes mode when
// there are more saved scenes than pads: tick the (up to 8) scenes that get a pad.
import { useEffect } from "react";
import { SCENE_SLOTS, type ScenesPane } from "./console/scenes";

export function ScenesModal({ pane, onClose }: { pane: ScenesPane; onClose: () => void }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
  const on = new Set(pane.visible.map((s) => s.name));
  const full = on.size >= SCENE_SLOTS;
  const toggle = (name: string) => {
    const next = new Set(on);
    if (!next.delete(name)) next.add(name);
    pane.setShown(pane.all.filter((s) => next.has(s.name)).map((s) => s.name));
  };
  return (
    <div className="absolute inset-0 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.55)", zIndex: 20 }}
      onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-label="Scenes" data-testid="scenes-modal"
        className="relative flex flex-col rounded-[10px] border border-line bg-panel shadow-xl"
        style={{ width: 400, maxHeight: 520, padding: 16, gap: 12 }}>
        <div className="flex items-center">
          <div className="font-semibold" style={{ fontSize: 13 }}>Scenes</div>
          <div className="text-dim" style={{ fontSize: 9.5, marginLeft: 10 }}>
            Pick which saved scenes get a pad ({on.size} of {SCENE_SLOTS}).
          </div>
          <button aria-label="Close" onClick={onClose} className="ml-auto text-dim hover:text-fg" style={{ fontSize: 14, width: 22 }}>✕</button>
        </div>
        <div className="flex min-h-0 flex-col overflow-y-auto" style={{ gap: 4, maxHeight: 420 }}>
          {pane.all.map((s) => (
            <label key={s.name} className="flex items-center" style={{ gap: 8, fontSize: 10.5 }}>
              <input type="checkbox" checked={on.has(s.name)} disabled={!on.has(s.name) && full}
                onChange={() => toggle(s.name)} aria-label={`Show ${s.name}`} />
              <span className="flex" style={{ gap: 2 }}>
                {s.colors.slice(0, 4).map((c, k) => <span key={k} className="rounded-full" style={{ width: 7, height: 7, background: c }} />)}
              </span>
              <span className="min-w-0 flex-1 truncate">{s.name}</span>
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}
