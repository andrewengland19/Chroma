export function mmss(ms: number | null | undefined): string {
  if (ms == null || !isFinite(ms) || ms < 0) return "0:00";
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}
