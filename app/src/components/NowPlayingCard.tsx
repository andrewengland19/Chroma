import { mmss } from "../lib/format";
import { useSelect, useSongPosition, useStore } from "../store";

export function NowPlayingCard() {
  const now = useSelect((s) => s.state?.now);
  const store = useStore();
  const pos = useSongPosition();
  const has = !!now?.title || !!now?.artist;
  const dur = now?.dur_ms ?? null;
  const frac = has && dur ? Math.max(0, Math.min(1, (pos ?? 0) / dur)) : 0;
  const art = store.artSrc(now?.art_url ?? null);
  return (
    <section data-testid="now-playing" className="absolute rounded-[8px] border border-line bg-panel"
      style={{ left: 349, top: 42, width: 420, height: 56 }}>
      <div className="absolute overflow-hidden rounded-[5px] bg-ctl" style={{ left: 10, top: 7, width: 41, height: 41 }}>
        {art && <img src={art} alt="" className="h-full w-full object-cover" draggable={false} />}
      </div>
      <div className="absolute truncate font-semibold" style={{ left: 62, top: 6, right: 40, fontSize: 13 }}>
        {has ? now!.title : "Nothing playing"}
      </div>
      <div className="absolute truncate text-dim" style={{ left: 62, top: 23, right: 120, fontSize: 10 }}>
        {has
          ? [now!.artist, now!.album].filter(Boolean).map((t, i) => (
              <span key={i}>{i > 0 && <span style={{ margin: "0 5px" }}>·</span>}{t}</span>
            ))
          : "Play something on the Apple TV"}
      </div>
      {has && now!.playing && (
        <svg aria-label="playing" className="absolute" style={{ right: 13, top: 12 }} width="9" height="10" viewBox="0 0 9 10">
          <path d="M0 0 L9 5 L0 10 Z" fill="var(--color-go)" />
        </svg>
      )}
      <div className="absolute rounded-full bg-ctl" style={{ left: 62, top: 43, width: 285, height: 3 }}>
        <div data-testid="progress" className="h-full rounded-full bg-accent" style={{ width: `${frac * 100}%` }} />
      </div>
      <div data-testid="time" className="absolute font-mono text-dim" style={{ right: 12, top: 38, fontSize: 8.5 }}>
        {has ? `${mmss(pos)} / ${mmss(dur)}` : "–:–– / –:––"}
      </div>
    </section>
  );
}
