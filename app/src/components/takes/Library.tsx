// Takes library (gui-2 left): search, one row per take, totals.
import { useState } from "react";
import { Panel } from "../ui";
import { mmss } from "../../lib/format";
import { shortDate, totalRecorded, type Summary } from "../../lib/takes";
import { useSelect } from "../../store";

const ROW = 64.5;

export function Library({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const takes = useSelect((s) => s.takes) ?? [];
  const recId = useSelect((s) => (s.state?.rec.state === "recording" ? s.state.rec.take_id : null));
  const playId = useSelect((s) => s.state?.play.take_id ?? null);
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const shown = needle
    ? takes.filter((t) => [t.title, t.artist, t.name, t.album].some((x) => x.toLowerCase().includes(needle)))
    : takes;
  return (
    <Panel style={{ left: 19, top: 45, width: 248, height: 629 }}>
      <input data-testid="takes-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search takes…"
        className="absolute rounded-[6px] border border-ctl-line bg-ctl text-fg outline-none placeholder:text-label"
        style={{ left: 11.5, top: 11.5, width: 224, height: 25, fontSize: 9, paddingLeft: 9 }} />
      <div className="absolute overflow-y-auto" style={{ left: 0, top: 48, width: 248, height: 545 }}>
        {shown.map((t, k) => (
          <Row key={t.id} t={t} top={k * ROW} selected={t.id === selected} active={t.id === recId || t.id === playId}
            onClick={() => onSelect(t.id)} />
        ))}
        {takes.length === 0 && (
          <div className="text-dim" style={{ padding: "20px 14px", fontSize: 9, lineHeight: "14px" }}>
            No takes yet. Play a song, press REC on the Console and perform; STOP saves the take.
          </div>
        )}
      </div>
      <div data-testid="takes-footer" className="absolute text-label" style={{ left: 12, top: 604, fontSize: 7.5 }}>
        {`${takes.length} take${takes.length === 1 ? "" : "s"}  ·  ${totalRecorded(takes)}`}
      </div>
    </Panel>
  );
}

function Row({ t, top, selected, active, onClick }: {
  t: Summary; top: number; selected: boolean; active: boolean; onClick: () => void;
}) {
  return (
    <button data-testid={`take-${t.id}`} onClick={onClick} aria-current={selected ? "true" : undefined}
      className="absolute text-left"
      style={{ left: 9, top: top + 1, width: 230, height: 59, borderRadius: 7,
        border: `1.2px solid ${selected ? "var(--color-accent)" : "transparent"}`,
        background: selected ? "#2b2840" : "transparent" }}>
      <span className="absolute" style={{ left: 9, top: 9.5, width: 40, height: 40, borderRadius: 5, background: t.art_hex ?? "#3a3a44" }} />
      <span className="absolute truncate font-semibold text-fg" style={{ left: 58, top: 5.5, width: 150, fontSize: 10.5, lineHeight: "15px" }}>{t.title || t.name}</span>
      <span className="absolute truncate text-dim" style={{ left: 58, top: 20.5, width: 160, fontSize: 9, lineHeight: "13px" }}>{t.artist}</span>
      <span className="absolute truncate text-label" style={{ left: 58, top: 33.5, width: 165, fontSize: 7.5, lineHeight: "12px", whiteSpace: "pre" }}>
        {`${mmss(t.dur_ms)}  ·  ${shortDate(t.created_ms)}  ·  ${t.groups_used.length} groups`}
      </span>
      {active && <span data-testid="take-active" className="absolute rounded-full"
        style={{ left: 211.5, top: 11.5, width: 7, height: 7, background: "var(--color-rec)" }} />}
    </button>
  );
}
