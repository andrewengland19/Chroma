// Take helpers for the Takes screen and the Console activity strip: lanes per group
// (level curve + flash blocks) from a take's header and events, and formatting.
import type { State, TakeEvent, TakeHeader, TakeSummary } from "../contracts";

export type Summary = TakeSummary;

export interface Lane {
  group_id: string;
  name: string;
  color: string;
  /** (p, level) points, level 0..1, from p = 0 to the end of the take */
  curve: [number, number][];
  /** flash down..up spans (ms) */
  flashes: [number, number][];
  kinds: { level: boolean; color: boolean; flash: boolean };
  events: number;
}

export const MAX_LANES = 8;

/** One lane per group (header.groups order). Groups with events come first in the
 *  selection, then empty ones fill up to MAX_LANES; the result keeps the header order. */
export function takeLanes(header: TakeHeader, events: TakeEvent[], end?: number): Lane[] {
  const groups = header.groups ?? {};
  const ids = Object.keys(groups);
  const endMs = end ?? Math.max(header.dur_ms ?? 0, ...events.map((e) => e.p), 1);
  // channel key -> level; channel key -> group
  const chLevel = new Map<string, number>();
  const chGroup = new Map<string, string>();
  (header.init?.channels ?? []).forEach((c, k) => {
    const key = `${"ABC"[Math.floor(k / 8)]}${(k % 8) + 1}`;
    chLevel.set(key, c.level);
    if (c.group_id) chGroup.set(key, c.group_id);
  });
  const groupLevel = (g: string) => {
    let m = 0;
    chGroup.forEach((gg, key) => {
      if (gg === g) m = Math.max(m, chLevel.get(key) ?? 0);
    });
    return m;
  };
  const lanes = new Map<string, Lane>();
  for (const g of ids) {
    lanes.set(g, { group_id: g, name: groups[g].name, color: groups[g].color, curve: [[0, groupLevel(g)]],
      flashes: [], kinds: { level: false, color: false, flash: false }, events: 0 });
  }
  const down = new Map<string, number>();
  for (const e of events) {
    const g = e.g ?? undefined;
    const key = e.b && typeof e.c === "number" ? `${e.b}${e.c}` : null;
    if (key && g) chGroup.set(key, g);
    const lane = g ? lanes.get(g) : undefined;
    if (!lane || e.a === "select" || e.a === "assign") continue;
    lane.events += 1;
    if (e.a === "level" && key && typeof e.v === "number") {
      chLevel.set(key, e.v);
      lane.kinds.level = true;
      lane.curve.push([e.p, groupLevel(g!)]);
    } else if (e.a === "hue" || e.a === "sat" || e.a === "palette") {
      lane.kinds.color = true;
    } else if (e.a === "flash" && key) {
      lane.kinds.flash = true;
      if (e.v === true) down.set(key, e.p);
      else if (down.has(key)) {
        lane.flashes.push([down.get(key)!, e.p]);
        down.delete(key);
      }
    }
  }
  down.forEach((p0, key) => {
    const lane = lanes.get(chGroup.get(key) ?? "");
    lane?.flashes.push([p0, Math.min(endMs, p0 + 250)]);
  });
  lanes.forEach((l) => l.curve.push([endMs, l.curve[l.curve.length - 1][1]]));
  const withEvents = ids.filter((g) => lanes.get(g)!.events > 0);
  const chosen = new Set([...withEvents, ...ids.filter((g) => !withEvents.includes(g))].slice(0, MAX_LANES));
  return ids.filter((g) => chosen.has(g)).map((g) => lanes.get(g)!);
}

export function laneSubtitle(l: Lane): string {
  const k = l.kinds;
  if (k.flash && !k.level && !k.color) return "flash only";
  if (k.level && k.color) return "level · color";
  if (k.level) return "level";
  if (k.color) return "color";
  return "level · color";
}

/** Song position as 01:42.310 */
export function clockMs(ms: number | null | undefined): string {
  const v = Math.max(0, Math.round(ms ?? 0));
  const m = Math.floor(v / 60000);
  const s = Math.floor((v % 60000) / 1000);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(v % 1000).padStart(3, "0")}`;
}

/** Duration as 03:25 */
export function clockS(ms: number | null | undefined): string {
  const v = Math.max(0, Math.floor((ms ?? 0) / 1000));
  return `${String(Math.floor(v / 60)).padStart(2, "0")}:${String(v % 60).padStart(2, "0")}`;
}

/** Elapsed as 00:01:42 */
export function hms(ms: number): string {
  const v = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(v / 3600);
  return `${String(h).padStart(2, "0")}:${String(Math.floor((v % 3600) / 60)).padStart(2, "0")}:${String(v % 60).padStart(2, "0")}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function shortDate(ms: number): string {
  const d = new Date(ms);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

export function longDate(ms: number): string {
  const d = new Date(ms);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function nudgeText(ms: number): string {
  return `${ms < 0 ? "−" : "+"} ${Math.abs(ms)} ms`;
}

export function totalRecorded(takes: Summary[]): string {
  const s = Math.round(takes.reduce((a, t) => a + (t.dur_ms ?? 0), 0) / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m recorded` : `${m}m recorded`;
}

/** Marker flag colour: DROP red, BREAK yellow, anything else grey (gui-2). */
export function markerColor(label: string): { bg: string; fg: string } {
  const l = label.toUpperCase();
  if (l.startsWith("DROP")) return { bg: "#e5484d", fg: "#1a0a0c" };
  if (l.startsWith("BREAK")) return { bg: "#e0a73f", fg: "#1a1206" };
  return { bg: "#8a8a94", fg: "#141418" };
}

/** The newest take for the song now playing (Console PLAY). */
export function takeForTrack(takes: Summary[], now: State["now"] | undefined): Summary | undefined {
  if (!now?.track_id && !now?.title) return undefined;
  return takes.find((t) => (t.track_id && t.track_id === now.track_id) || (t.title === now.title && t.artist === now.artist));
}
