import React from "react";
import { Track } from "../types";

interface Props {
  track: Track | null;
}

export function NowPlaying({ track }: Props) {
  const title = track?.title || "—";
  const sub =
    track && (track.artist || track.album)
      ? [track.artist, track.album].filter(Boolean).join(" — ")
      : "Not playing";

  return (
    <div
      className="px-4 py-2 h-12 flex flex-col justify-center accent-transition"
      style={{ background: "var(--accent-muted)" }}
    >
      <div className="font-display text-[14px] font-semibold text-fg truncate">
        {title}
      </div>
      <div className="font-body text-[12px] text-fg-dim truncate">{sub}</div>
    </div>
  );
}
