// Agent tab, top right: the look as read back from the bulbs after the run (or undo),
// placed on the Room layout where a bulb's label matches a fixture's.
import { useSelect } from "../../store";
import { Panel } from "../ui";

const W = 541;
const H = 230;

export function LookPanel() {
  const look = useSelect((s) => s.state?.agent.look) ?? [];
  const room = useSelect((s) => s.state?.room) ?? [];
  const pos = new Map(room.map((r) => [r.label.toLowerCase(), r]));
  const shown = (l: (typeof look)[number]) => (l.power === "on" && l.hex ? l.hex : null);
  return (
    <Panel style={{ left: 594, top: 45, width: 565, height: 470 }}>
      <div className="label absolute" style={{ left: 12, top: 10 }}>
        Look <span style={{ margin: "0 3px" }}>·</span> <span className="normal-case">read back from the bulbs</span>
      </div>
      <div data-testid="look-canvas" className="absolute overflow-hidden rounded-[7px] border border-line bg-well"
        style={{ left: 12, top: 26, width: W, height: H }}>
        {look.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-dim" style={{ fontSize: 10 }}>
            The room appears here after the agent answers.
          </div>
        )}
        {look.map((l) => {
          const r = pos.get(l.label.toLowerCase());
          if (!r) return null;
          const c = shown(l);
          const x = r.x * W;
          const y = r.y * H;
          return (
            <div key={l.label}>
              {c && (
                <div className="pointer-events-none absolute rounded-full"
                  style={{ left: x - 50, top: y - 50, width: 100, height: 100, opacity: 0.35 + 0.65 * (l.brightness ?? 1),
                    background: `radial-gradient(circle, ${c}80 0%, ${c}38 38%, transparent 70%)` }} />
              )}
              <div className="absolute rounded-full" title={l.label}
                style={{ left: x - 8, top: y - 8, width: 16, height: 16, background: c ?? "#3a3a42", border: "1.3px solid #f3f3f6" }} />
              <div className="absolute whitespace-nowrap text-dim" style={{ left: x, top: y + 11, fontSize: 8, transform: "translateX(-50%)" }}>
                {l.label}
              </div>
            </div>
          );
        })}
      </div>
      <ul data-testid="look-list" className="absolute grid overflow-y-auto"
        style={{ left: 12, top: 266, width: W, height: 192, gridTemplateColumns: "1fr 1fr", columnGap: 16, rowGap: 6, alignContent: "start" }}>
        {look.map((l) => {
          const c = shown(l);
          return (
            <li key={l.label} data-testid={`look-${l.label}`} className="flex items-center" style={{ gap: 8, fontSize: 10, height: 18 }}>
              <span className="shrink-0 rounded-[4px] border border-line" style={{ width: 18, height: 14, background: c ?? "transparent" }} />
              <span className="truncate text-fg" style={{ flex: 1 }}>{l.label}</span>
              <span className="font-mono text-dim" style={{ fontSize: 9 }}>
                {l.power === "on" ? `${l.hex ?? ""} ${Math.round((l.brightness ?? 0) * 100)}%` : l.power}
              </span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
