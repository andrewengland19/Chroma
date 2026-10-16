// Takes timeline (gui-2 middle): ruler with markers, one lane per group (level curve +
// flash blocks), playhead. Click = seek (when the take's song is playing); double-click
// the ruler or press M = add a marker; click a marker flag = remove it.
import { useEffect, useMemo, useRef, useState } from "react";
import { Panel } from "../ui";
import { laneSubtitle, markerColor, takeLanes, type Lane } from "../../lib/takes";
import type { TakeEvent, TakeHeader } from "../../contracts";
import { useStore } from "../../store";

// canvas coordinates (gui-2), relative to the panel at (282, 113)
const PANEL = { left: 282, top: 113, width: 877, height: 419 };
const X0 = 409 - PANEL.left;          // plot left
const PW = 735;                       // plot width = the whole song
const LANE_TOP = 151.5 - PANEL.top;
const LANE_PITCH = 46.5;
const LANE_H = 40;
const RULER = { top: 121 - PANEL.top, height: 21 };

function curvePath(l: Lane, x: (p: number) => number): { line: string; area: string } {
  const y = (v: number) => LANE_H - 3 - v * 34;
  const pts = l.curve.map(([p, v]) => `${x(p).toFixed(1)},${y(v).toFixed(1)}`);
  const line = `M${pts.join(" L")}`;
  const last = l.curve[l.curve.length - 1];
  return { line, area: `${line} L${x(last[0]).toFixed(1)},${LANE_H} L${x(l.curve[0][0]).toFixed(1)},${LANE_H} Z` };
}

export function Timeline({ header, events, pos, canSeek }: {
  header: TakeHeader | null; events: TakeEvent[]; pos: number; canSeek: boolean;
}) {
  const store = useStore();
  const dur = Math.max(1, header?.dur_ms ?? Math.max(1, ...events.map((e) => e.p)));
  const lanes = useMemo(() => (header ? takeLanes(header, events, dur) : []), [header, events, dur]);
  const xr = (p: number) => (Math.min(dur, Math.max(0, p)) / dur) * PW;      // within the plot
  const [adding, setAdding] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!header || e.metaKey || e.ctrlKey || e.altKey) return;
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "m" || e.key === "M") {
        e.preventDefault();
        setAdding(Math.round(pos));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [header, pos]);

  const posAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    const zoom = r.width / PANEL.width || 1;
    return Math.round(Math.min(1, Math.max(0, (clientX - r.left) / zoom - X0) / PW) * dur);
  };
  const seek = (clientX: number) => {
    if (!header || !canSeek) return;
    const p = posAt(clientX);
    if (p >= 0) void store.op("play.seek", { pos_ms: p });
  };
  const addMarker = (label: string) => {
    if (header && adding != null && label.trim()) {
      void store.op("takes.marker.add", { take_id: header.id, p_ms: adding, label: label.trim().slice(0, 16) });
    }
    setAdding(null);
  };
  const ticks = [];
  for (let t = 0; t <= dur; t += 15000) ticks.push(t);
  // a tick label hides under a marker flag (gui-2 shows 0:00, 1:00, 1:30, 3:00 around four flags)
  const flagW = (label: string) => 13 + label.length * 4.6;
  const covered = (t: number) => (header?.markers ?? []).some((m) =>
    xr(t) + 12 > xr(m.p) - 2 && xr(t) - 12 < xr(m.p) + flagW(m.label) + 2);

  return (
    <Panel style={PANEL}>
      <div ref={ref} data-testid="timeline" className="absolute inset-0">
        {/* ruler */}
        <div data-testid="ruler" className="absolute rounded-[4px] bg-well"
          title={canSeek ? "Click to seek · double-click or M to add a marker" : "Double-click or M to add a marker"}
          style={{ left: X0 - 7, top: RULER.top, width: PW + 7, height: RULER.height, cursor: canSeek ? "pointer" : "default" }}
          onClick={(e) => seek(e.clientX)}
          onDoubleClick={(e) => header && setAdding(posAt(e.clientX))}>
          {ticks.map((t) => (
            <span key={t}>
              <span className="absolute" style={{ left: 7 + xr(t), top: 11.5, width: 1, height: 8.5, background: "#5a5a66" }} />
              {t % 30000 === 0 && !covered(t) && (
                <span className="absolute font-mono text-dim"
                  style={{ left: 7 + xr(t) - 15, width: 30, textAlign: "center", top: 1.5, fontSize: 7.2 }}>
                  {`${Math.floor(t / 60000)}:${String((t / 1000) % 60).padStart(2, "0")}`}
                </span>
              )}
            </span>
          ))}
        </div>
        {(header?.markers ?? []).map((m) => {
          const c = markerColor(m.label);
          return (
            <span key={`${m.p}-${m.label}`}>
              <button data-testid={`marker-${m.label}`} title={`${m.label} at ${(m.p / 1000).toFixed(1)} s — click to remove`}
                onClick={() => {
                  if (header && window.confirm(`Remove marker ${m.label}?`)) {
                    void store.op("takes.marker.remove", { take_id: header.id, p_ms: m.p, label: m.label });
                  }
                }}
                className="absolute font-bold"
                style={{ left: X0 + xr(m.p), top: RULER.top + 0.5, height: 12, padding: "0 7px 0 4px", fontSize: 6.6, lineHeight: "12px",
                  background: c.bg, color: c.fg, clipPath: "polygon(0 0, calc(100% - 4px) 0, 100% 50%, calc(100% - 4px) 100%, 0 100%)" }}>
                {m.label}
              </button>
              <span className="pointer-events-none absolute"
                style={{ left: X0 + xr(m.p), top: LANE_TOP - 4, width: 0, height: lanes.length * LANE_PITCH,
                  borderLeft: `1px dashed ${c.bg}`, opacity: 0.35 }} />
            </span>
          );
        })}
        {adding != null && (
          <input data-testid="marker-input" autoFocus defaultValue="DROP" maxLength={16}
            onFocus={(e) => e.target.select()}
            onKeyDown={(e) => {
              if (e.key === "Enter") addMarker((e.target as HTMLInputElement).value);
              if (e.key === "Escape") setAdding(null);
            }}
            onBlur={() => setAdding(null)}
            className="absolute rounded-[3px] border border-accent bg-ctl font-bold text-fg outline-none"
            style={{ left: Math.min(X0 + xr(adding), X0 + PW - 60), top: RULER.top + 1, width: 60, height: 12, fontSize: 6.5, paddingLeft: 3 }} />
        )}
        {/* lanes */}
        {lanes.map((l, k) => (
          <LaneRow key={l.group_id} lane={l} top={LANE_TOP + k * LANE_PITCH} xr={xr} onSeek={seek} canSeek={canSeek} />
        ))}
        {header == null && (
          <div className="absolute flex items-center justify-center text-label" style={{ left: X0, top: LANE_TOP, width: PW, height: 360, fontSize: 9 }}>
            Select a take
          </div>
        )}
        {/* playhead */}
        {header && (
          <div data-testid="playhead" className="pointer-events-none absolute" style={{ left: X0 + xr(pos) - 5, top: RULER.top + RULER.height + 1 }}>
            <svg width="10" height="8" viewBox="0 0 10 8" className="absolute"><path d="M0 0 H10 L5 7 Z" fill="var(--color-red)" /></svg>
            <span className="absolute" style={{ left: 4.5, top: 6, width: 1.5, height: lanes.length * LANE_PITCH + 4, background: "var(--color-red)" }} />
          </div>
        )}
        <div className="absolute truncate rounded-[3px] border border-line text-label"
          style={{ left: X0, top: 408, width: PW, height: 11, fontSize: 5.5, lineHeight: "10px", paddingLeft: 6 }}>
          audio features (beat · bands · onsets) — phase 2
        </div>
      </div>
    </Panel>
  );
}

function LaneRow({ lane, top, xr, onSeek, canSeek }: {
  lane: Lane; top: number; xr: (p: number) => number; onSeek: (clientX: number) => void; canSeek: boolean;
}) {
  const empty = lane.events === 0;
  const { line, area } = useMemo(() => curvePath(lane, xr), [lane, xr]);
  return (
    <div data-testid={`lane-${lane.group_id}`}>
      <div className="absolute rounded-[6px] bg-ctl" style={{ left: 15, top, width: 103, height: LANE_H }}>
        <span className="absolute rounded-full" style={{ left: 7, top: 16.5, width: 7, height: 7, background: lane.color }} />
        <span className="absolute truncate font-bold text-fg" style={{ left: 19, top: 8.5, width: 80, fontSize: 9, lineHeight: "12px" }}>{lane.name}</span>
        <span className="absolute truncate text-label" style={{ left: 19, top: 22.5, width: 80, fontSize: 6.5, lineHeight: "10px" }}>
          {laneSubtitle(lane)}
        </span>
      </div>
      <div className="absolute overflow-hidden rounded-[4px] bg-well" onClick={(e) => onSeek(e.clientX)}
        style={{ left: X0, top, width: PW, height: LANE_H, cursor: canSeek ? "pointer" : "default" }}>
        {empty ? (
          <div className="flex h-full items-center justify-center text-label" style={{ fontSize: 7 }}>no events in this take</div>
        ) : (
          <svg width={PW} height={LANE_H} className="absolute inset-0">
            <path d={area} fill={lane.color} fillOpacity={0.32} />
            <path d={line} fill="none" stroke={lane.color} strokeWidth={1.2} strokeLinejoin="round" />
            {lane.flashes.map(([a, b], k) => (
              <rect key={k} x={xr(a)} y={6} width={Math.max(4, xr(b) - xr(a))} height={LANE_H - 11} rx={1.5} fill={lane.color} />
            ))}
          </svg>
        )}
      </div>
    </div>
  );
}
