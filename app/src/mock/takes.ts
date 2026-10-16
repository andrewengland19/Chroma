// Mock take library for the browser engine: the six takes of docs/mvp/gui-2-takes.png.
// "Older" is generated from fader gestures and flash bursts shaped like the mockup's
// lanes (sections: verse, DROP at 1:12, BREAK at 1:58, DROP 2 at 2:30). Real take
// semantics live in hub/takes.py; this mirrors the ops so the UI can be exercised.
import type { Activity, State, TakeEvent, TakeHeader, TakeInit } from "../contracts";
import type { Reply } from "../ipc/types";
import type { Summary } from "../lib/takes";

const GROUPS: Record<string, { name: string; color: string }> = {
  "lr-all": { name: "LR ALL", color: "#e5484d" }, bed: { name: "BED", color: "#f59e5b" },
  counter: { name: "COUNTER", color: "#f2c94c" }, playbill: { name: "PLAYBILL", color: "#4ade80" },
  "lr-l": { name: "LR L", color: "#2dd4bf" }, "lr-r": { name: "LR R", color: "#3b82f6" },
  accent: { name: "ACCENT", color: "#a855f7" }, fx: { name: "FX", color: "#f472b6" },
};
const BANK_A = ["lr-all", "bed", "counter", "playbill", "lr-l", "lr-r", "accent", "fx"];

function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

function init(levels: Record<string, number>): TakeInit {
  const channels = Array.from({ length: 24 }, (_, k) => {
    const g = k < 8 ? BANK_A[k] : null;
    return { group_id: g, level: g ? levels[g] ?? 0 : 0, hue: 0, sat: 0 };
  });
  return { bank: "A", fader_fn: "level", master: 0.9, blackout: false, channels };
}

/** Section envelope 0..1 (low verse, high drop, low break, high drop 2). */
function envelope(t: number, rise = 62): number {
  const ramp = (a: number, b: number) => Math.min(1, Math.max(0, (t - a) / (b - a)));
  if (t < 118) return ramp(rise, rise + 13);
  if (t < 150) return 1 - ramp(118, 125);
  return ramp(150, 160);
}

const LEVELS: Record<string, [number, number, number]> = {   // low, high, rise (s)
  "lr-all": [0.3, 0.85, 62], counter: [0.22, 0.78, 66], "lr-l": [0.3, 0.8, 64],
  "lr-r": [0.28, 0.85, 55], accent: [0.15, 0.85, 62], fx: [0.3, 0.55, 64],
};

function olderEvents(dur: number): TakeEvent[] {
  const r = rng(7);
  const out: TakeEvent[] = [];
  const w0 = Date.UTC(2026, 9, 9, 19, 48);
  BANK_A.forEach((g, k) => {
    const lv = LEVELS[g];
    if (!lv) return;
    const ch = k + 1;
    const ev = (p: number, a: TakeEvent["a"], v: number | boolean): TakeEvent => ({
      p: Math.round(p), w: w0 + Math.round(p),
      i: a === "flash" ? { intent: "flash", ch, down: v as boolean } : { intent: "fader", ch, v: v as number },
      g, b: "A", c: ch, a, v,
    });
    for (let t = 0.5; t < dur / 1000; t += 2.4 + r() * 2.2) {
      const base = lv[0] + (lv[1] - lv[0]) * envelope(t, lv[2]);
      const v = Math.min(1, Math.max(0, base + (r() - 0.5) * 0.09));
      out.push(ev(t * 1000, "level", +v.toFixed(3)));
      out.push(ev(t * 1000 + 350, "level", +Math.min(1, v + 0.01).toFixed(3)));
    }
    if (g === "counter" || g === "lr-l" || g === "fx") {
      for (const at of [72, 76, 80, 84, 150, 154, 158]) {
        out.push(ev(at * 1000, "flash", true));
        out.push(ev(at * 1000 + 1500, "flash", false));
      }
    }
  });
  return out.sort((a, b) => a.p - b.p);
}

function randomEvents(seed: number, dur: number, groups: string[]): TakeEvent[] {
  const r = rng(seed);
  const out: TakeEvent[] = [];
  groups.forEach((g) => {
    const ch = BANK_A.indexOf(g) + 1;
    for (let t = 1; t < dur / 1000; t += 3 + r() * 6) {
      const v = +(0.2 + 0.7 * r()).toFixed(3);
      out.push({ p: Math.round(t * 1000), w: 0, i: { intent: "fader", ch, v }, g, b: "A", c: ch, a: "level", v });
    }
  });
  return out.sort((a, b) => a.p - b.p);
}

interface MockTake { header: TakeHeader; events: TakeEvent[] }

function make(id: string, title: string, artist: string, album: string, dur: number, created: number,
  art: string, groups: string[], seed: number, extra: Partial<TakeHeader> = {}): MockTake {
  const events = id === "20261009-214800" ? olderEvents(dur) : randomEvents(seed, dur, groups);
  const header: TakeHeader = {
    v: 1, id, name: `${title} — draft 01`, track_id: id === "20261009-214800" ? "a1b2c3d4e5f60718" : `mock-${seed}`,
    title, artist, album, dur_ms: dur, fixtures_rev: 3, nudge_ms: 0, training: true, markers: [],
    created_ms: created, performer: "You", sync_err_ms: 8, art_hex: art,
    init: init(Object.fromEntries(groups.map((g) => [g, LEVELS[g]?.[0] ?? 0.3]))),
    groups: Object.fromEntries(BANK_A.map((g) => [g, GROUPS[g]])),
    stats: { events: events.length, groups_used: groups, length_ms: dur }, ...extra,
  };
  return { header, events };
}

const T = (y: number, mo: number, d: number, h: number, mi: number) => new Date(y, mo - 1, d, h, mi).getTime();

export function mockTakes(): MockTake[] {
  const older = make("20261009-214800", "Older", "Sasha Alex Sloan", "Loser - EP", 205000, T(2026, 10, 9, 21, 48),
    "#c9343f", ["lr-all", "counter", "lr-l", "lr-r", "accent", "fx"], 1, {
      name: "Older — draft 03", nudge_ms: 12,
      markers: [{ p: 20000, label: "VERSE" }, { p: 72000, label: "DROP" }, { p: 118000, label: "BREAK" },
        { p: 150000, label: "DROP 2" }],
    });
  older.header.stats!.events = 412;            // the mockup's figure (the generated curve is denser)
  return [
    older,
    make("20261008-201500", "Levitating (Remix)", "Dua Lipa", "Club Future Nostalgia", 242000, T(2026, 10, 8, 20, 15),
      "#d4935a", BANK_A, 2),
    make("20261006-223000", "Strobe", "deadmau5", "For Lack of a Better Name", 637000, T(2026, 10, 6, 22, 30),
      "#d4b84a", BANK_A, 3),
    make("20261003-190200", "Midnight City", "M83", "Hurry Up, We're Dreaming", 243000, T(2026, 10, 3, 19, 2),
      "#5ab070", ["lr-all", "counter", "lr-l", "lr-r", "fx"], 4),
    make("20260928-211100", "Opus", "Eric Prydz", "Opus", 543000, T(2026, 9, 28, 21, 11),
      "#2aa89a", ["lr-all", "bed", "counter", "lr-l", "lr-r", "accent", "fx"], 5),
    make("20260925-200000", "Phantom", "Justice", "†", 261000, T(2026, 9, 25, 20, 0),
      "#3a78d8", ["lr-all", "counter", "lr-r", "fx"], 6),
  ];
}

/** State.activity for a take's events up to `pos` (spans like hub/takes.py activity_lanes). */
export function mockActivity(t: MockTake, pos: number, source: "rec" | "play"): Activity {
  const by = new Map<string, [number, number][]>();
  const down = new Map<string, number>();
  for (const e of t.events) {
    if (e.p > pos || !e.g) continue;
    let span: [number, number];
    if (e.a === "flash") {
      const k = `${e.b}${e.c}`;
      if (e.v === true) { down.set(k, e.p); continue; }
      span = [down.get(k) ?? e.p, e.p];
    } else span = [e.p, e.p + 150];
    const l = by.get(e.g) ?? [];
    const last = l[l.length - 1];
    if (last && span[0] - last[1] <= 3000) last[1] = Math.max(last[1], span[1]);
    else l.push(span);
    by.set(e.g, l);
  }
  const lanes = BANK_A.filter((g) => by.has(g)).map((g) => ({ group_id: g, color: GROUPS[g].color, spans: by.get(g)! }));
  return { source, dur_ms: t.header.dur_ms, lanes };
}

/** The takes.* / rec.* / play.* ops of the mock engine. */
export class MockTakeStore {
  takes = mockTakes();
  constructor(private getState: () => State, private patch: (p: Record<string, unknown>) => void) {}

  private find(id: unknown) {
    return this.takes.find((t) => t.header.id === id);
  }

  summaries(): Summary[] {
    return this.takes.map(({ header: h }) => ({
      id: h.id, name: h.name, title: h.title, artist: h.artist, album: h.album, dur_ms: h.dur_ms,
      created_ms: h.created_ms ?? 0, events: h.stats?.events ?? 0, groups_used: h.stats?.groups_used ?? [],
      training: h.training, art_hex: h.art_hex ?? null, track_id: h.track_id,
    }));
  }

  private bump() {
    this.patch({ takes_rev: this.getState().takes_rev + 1 });
  }

  handle(req: Record<string, unknown>): Reply {
    const t = this.find(req.take_id);
    const s = this.getState();
    const need = () => (t ? null : { ok: false, error: `no take '${String(req.take_id)}'` });
    switch (req.op) {
      case "takes.list":
        return { ok: true, takes: this.summaries() };
      case "takes.get":
        return need() ?? { ok: true, header: structuredClone(t!.header), events: structuredClone(t!.events) };
      case "takes.rename":
        if (need()) return need()!;
        t!.header.name = String(req.name);
        this.bump();
        return { ok: true };
      case "takes.set_flag":
        if (need()) return need()!;
        t!.header.training = !!req.value;
        this.bump();
        return { ok: true };
      case "takes.marker.add":
        if (need()) return need()!;
        t!.header.markers = [...t!.header.markers, { p: Number(req.p_ms), label: String(req.label) }].sort((a, b) => a.p - b.p);
        this.bump();
        return { ok: true };
      case "takes.marker.remove":
        if (need()) return need()!;
        t!.header.markers = t!.header.markers.filter((m) => m.p !== req.p_ms);
        this.bump();
        return { ok: true };
      case "take.nudge":
        if (need()) return need()!;
        t!.header.nudge_ms = Number(req.ms);
        this.bump();
        return { ok: true };
      case "takes.export":
        if (need()) return need()!;
        return { ok: true, filename: `${t!.header.id}_take.jsonl`,
          jsonl: [t!.header, ...t!.events].map((x) => JSON.stringify(x)).join("\n") + "\n" };
      case "takes.duplicate": {
        if (need()) return need()!;
        const copy = structuredClone(t!);
        copy.header.id = `${t!.header.id}-1`;
        copy.header.name = `${t!.header.name} copy`;
        this.takes.unshift(copy);
        this.bump();
        return { ok: true, take_id: copy.header.id };
      }
      case "takes.delete":
        if (need()) return need()!;
        this.takes = this.takes.filter((x) => x !== t);
        this.bump();
        return { ok: true };
      case "rec.arm":
      case "rec.start": {
        if (!s.now.playing) {
          if (req.op === "rec.start") return { ok: false, error: "nothing is playing" };
          this.patch({ rec: { ...s.rec, state: "armed", take_id: (req.take_id as string) ?? null } });
          return { ok: true };
        }
        const name = t ? t.header.name : `${s.now.title} — draft 0${this.takes.filter((x) => x.header.title === s.now.title).length + 1}`;
        this.patch({ who: "board", rec: { state: "recording", take_id: t?.header.id ?? "20261016-120000", elapsed_ms: 0,
          events: 0, mode: (req.mode as string) ?? "replace", name, groups_touched: 0 } });
        return { ok: true };
      }
      case "rec.stop":
        this.patch({ rec: { ...s.rec, state: "idle", elapsed_ms: 0, events: 0, groups_touched: 0 },
          activity: { source: null, dur_ms: null, lanes: [] } });
        this.bump();
        return { ok: true };
      case "play.start": {
        if (need()) return need()!;
        if (t!.header.track_id !== s.now.track_id && t!.header.title !== s.now.title) {
          return { ok: false, error: `this take is for “${t!.header.title}”; play that song on the Apple TV first` };
        }
        const pos = s.now.pos_ms ?? 0;
        this.patch({ who: "board", play: { state: "playing", take_id: t!.header.id, pos_ms: pos, loop: req.loop ?? s.play.loop },
          activity: mockActivity(t!, t!.header.dur_ms ?? pos, "play") });
        return { ok: true };
      }
      case "play.stop":
        this.patch({ play: { ...s.play, state: "idle", take_id: null }, activity: { source: null, dur_ms: null, lanes: [] } });
        return { ok: true };
      case "play.seek":
        this.patch({ now: { pos_ms: Number(req.pos_ms) } });
        return { ok: true };
      default:
        return { ok: false, error: `unknown op '${String(req.op)}'` };
    }
  }
}
