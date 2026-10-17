// In-browser stand-in for chromad, so the UI runs (and can be screenshotted) without
// Tauri. Scenarios:
//   mockup-console   the exact data of docs/mvp/gui-1-console.png (static)
//   mockup-takes     docs/mvp/gui-2-takes.png: "Older" playing at 1:42.310 (static)
//   live             same data, playing: position advances, controls respond
//   empty            engine up, nothing playing
// Real engine semantics live in hub/ (tested with pytest); this only mirrors enough
// of them for the UI to be exercised by hand and by component tests.
import mockupState from "../../../contracts/examples/state/ok-mockup-console.json";
import type { ChannelState, IpcEvent, State } from "../contracts";
import { hexToHsv, hsvToHex } from "../lib/color";
import { applyMergePatch } from "../lib/mergePatch";
import type { EngineStatus, IpcClient, Reply } from "../ipc/types";
import { MockAgent } from "./agent";
import { MOCK_ART } from "./art";
import { MockRig } from "./rig";
import { MockTakeStore, mockActivity, mockTakes } from "./takes";

export const MOCK_STATUS: EngineStatus = {
  phase: "connected",
  connected: true,
  supervised: false,
  pid: null,
  fake: true,
  message: "mock engine (browser)",
  socket: "mock",
};

export type Scenario = "mockup-console" | "mockup-setup" | "mockup-takes" | "live" | "empty";

function baseState(scenario: Scenario): State {
  const s = structuredClone(mockupState) as State;
  if (scenario === "empty") {
    s.now = { track_id: "", title: "", artist: "", album: "", playing: false, pos_ms: null, dur_ms: null, art_url: null };
    s.rec = { state: "idle", take_id: null, elapsed_ms: 0, events: 0, mode: "replace", name: "", groups_touched: 0 };
    s.room = s.room.map((r) => ({ ...r, hex: null }));
  }
  if (scenario === "live" || scenario === "mockup-takes") {
    s.rec = { state: "idle", take_id: null, elapsed_ms: 0, events: 0, mode: "replace", name: "", groups_touched: 0 };
  }
  const older = mockTakes()[0];
  if (scenario === "mockup-console") s.activity = mockActivity(older, 102000, "rec");
  if (scenario === "mockup-takes") {
    s.now = { ...s.now, pos_ms: 102310 };
    s.play = { state: "playing", take_id: older.header.id, pos_ms: 102310, loop: false };
    s.activity = mockActivity(older, older.header.dur_ms ?? 0, "play");
  }
  return s;
}

export class MockEngine implements IpcClient {
  state: State;
  private events = new Set<(e: IpcEvent) => void>();
  private timer: ReturnType<typeof setInterval> | null = null;
  readonly requests: Record<string, unknown>[] = [];
  readonly rig = new MockRig(
    (rev) => this.patch({ fixtures_rev: rev }),
    // like rigsvc.room(): the Room shows a fixture where the rig says it is
    (fx) => {
      if (this.state.room.some((r) => r.fixture_id === fx.id && (r.x !== fx.x || r.y !== fx.y)))
        this.patch({ room: this.state.room.map((r) => (r.fixture_id === fx.id ? { ...r, x: fx.x, y: fx.y } : r)) });
    },
  );
  readonly takes = new MockTakeStore(() => this.state, (p) => this.patch(p));
  readonly agent = new MockAgent(() => this.state, (p) => this.patch(p));

  constructor(readonly scenario: Scenario = "live") {
    this.state = baseState(scenario);
    this.state.fixtures_rev = this.rig.rig.rev;
    if (scenario === "live") this.timer = setInterval(() => this.tick(250), 250);
  }

  dispose() {
    if (this.timer) clearInterval(this.timer);
    this.agent.dispose();
  }

  tick(ms: number) {
    const n = this.state.now;
    if (!n.playing || n.pos_ms == null) return;
    const pos = Math.min(n.dur_ms ?? Infinity, n.pos_ms + ms);
    this.patch({ now: { pos_ms: pos } });
  }

  patch(p: Record<string, unknown>) {
    this.state = applyMergePatch(this.state, p);
    this.emit({ ev: "patch", patch: p });
  }

  emit(e: IpcEvent) {
    this.events.forEach((cb) => cb(e));
  }

  artSrc(url: string | null): string | null {
    return url ? MOCK_ART : null;
  }

  async request(req: Record<string, unknown>): Promise<Reply> {
    this.requests.push(req);
    switch (req.op) {
      case "get_state":
        return { ok: true, state: structuredClone(this.state) };
      case "subscribe":
        queueMicrotask(() => this.emit({ ev: "state", state: structuredClone(this.state) }));
        return { ok: true };
      case "set_art_mode": {
        const mode = req.mode as State["art_mode"];
        if (!["det", "paint", "ai"].includes(mode)) return { ok: false, error: "mode must be one of det, paint, ai" };
        this.patch({ art_mode: mode });
        return { ok: true };
      }
      case "refresh":
        return { ok: true, result: { ok: true, message: "lights refreshed" } };
      case "intent": {
        const i = req.intent as { intent: string; [k: string]: unknown };
        if (i?.intent === "who" && (i.who === "auto" || i.who === "board")) {
          this.patch({ who: i.who });
          return { ok: true };
        }
        const r = this.console(i);
        if (r.ok && this.state.rec.state === "recording" && !["palette_store", "cue_store"].includes(i.intent)) {
          this.patch({ rec: { events: this.state.rec.events + 1 } });
        }
        return r;
      }
      default:
        if (typeof req.op === "string" && /^(fixtures|groups|banks|spaces)\./.test(req.op)) return this.rig.handle(req);
        if (typeof req.op === "string" && /^(takes|take|rec|play)\./.test(req.op)) return this.takes.handle(req);
        if (typeof req.op === "string" && req.op.startsWith("agent.")) return this.agent.handle(req);
        if (typeof req.op === "string" && req.op.startsWith("scenes.")) return this.agent.handleScenes(req);
        return { ok: false, error: `unknown op '${String(req.op)}'` };
    }
  }

  /** A small mirror of hub/console.py's intent rules (no pickup, no arbitration). */
  private looks = new Map<number, ChannelState[]>();

  private console(i: { intent: string; [k: string]: unknown }): Reply {
    const s = this.state;
    const chans = s.channels.map((c) => ({ ...c }));
    const find = (bank: string, ch: number) => chans.find((c) => c.bank === bank && c.ch === ch)!;
    const touched = () => {
      const lt = s.last_touched;
      return lt ? find(lt.bank, lt.ch) : null;
    };
    const patchChans = (extra: Record<string, unknown> = {}) => this.patch({ channels: chans, ...extra });
    switch (i.intent) {
      case "fader": {
        const v = Number(i.v);
        if (!(v >= 0 && v <= 1)) return { ok: false, error: "v must be 0..1" };
        if (i.ch === "master") {
          this.patch({ master: v });
          return { ok: true };
        }
        const c = find(s.bank, Number(i.ch));
        if (s.fader_fn === "level") c.level = v;
        else if (s.fader_fn === "hue") c.hue = Math.min(v * 360, 359.9);
        else c.sat = v;
        patchChans({ last_touched: { bank: c.bank, ch: c.ch }, cue_active: null });
        return { ok: true };
      }
      case "flash": {
        const c = find(s.bank, Number(i.ch));
        c.flash = !!i.down;
        patchChans(i.down ? { last_touched: { bank: c.bank, ch: c.ch } } : {});
        return { ok: true };
      }
      case "assign": {
        const c = find(s.bank, Number(i.ch));
        if ("group_id" in i) c.group_id = (i.group_id as string | null) ?? null;
        patchChans({ last_touched: { bank: c.bank, ch: c.ch } });
        return { ok: true };
      }
      case "bank":
        chans.forEach((c) => { c.engaged = false; });
        patchChans({ bank: i.bank });
        return { ok: true };
      case "fader_fn":
        chans.forEach((c) => { c.engaged = false; });
        patchChans({ fader_fn: i.fn });
        return { ok: true };
      case "hue":
      case "sat": {
        const c = touched();
        if (!c) return { ok: false, error: "touch a channel first (fader, FLASH or ASSIGN)" };
        if (i.intent === "hue") c.hue = typeof i.delta === "number" ? (((c.hue + i.delta * 5) % 360) + 360) % 360 : Number(i.v);
        else c.sat = typeof i.delta === "number" ? Math.min(1, Math.max(0, c.sat + i.delta * 0.02)) : Number(i.v);
        patchChans({ cue_active: null });
        return { ok: true };
      }
      case "palette_apply": {
        const c = touched();
        if (!c) return { ok: false, error: "touch a channel first (fader, FLASH or ASSIGN)" };
        const p = hexToHsv(s.palette[Number(i.i)]);
        c.hue = p.h;
        c.sat = p.s;
        patchChans({ cue_active: null });
        return { ok: true };
      }
      case "palette_store": {
        const c = touched();
        if (!c) return { ok: false, error: "touch a channel first (fader, FLASH or ASSIGN)" };
        const palette = [...s.palette];
        palette[Number(i.i)] = hsvToHex(c.hue, c.sat, 1);
        this.patch({ palette });
        return { ok: true };
      }
      case "cue_store": {
        const n = Number(i.i);
        this.looks.set(n, chans);
        this.patch({ cues: s.cues.map((c) => (c.id === n ? { ...c, stored: true } : c)), cue_active: n });
        return { ok: true };
      }
      case "cue_recall": {
        const n = Number(i.i);
        const cue = s.cues[n];
        if (!cue?.stored) return { ok: false, error: `cue ${n + 1} (${cue?.name}) is empty` };
        const look = this.looks.get(n);
        if (look) look.forEach((l, k) => Object.assign(chans[k], { level: l.level, hue: l.hue, sat: l.sat }));
        patchChans({ cue_active: n });
        return { ok: true };
      }
      case "blackout":
        this.patch({ blackout: !!i.on });
        return { ok: true };
      default:
        return { ok: false, error: `unknown intent '${String(i?.intent)}'` };
    }
  }

  onEvent(cb: (e: IpcEvent) => void) {
    this.events.add(cb);
    return () => this.events.delete(cb);
  }

  onStatus(_cb: (s: EngineStatus) => void) {
    return () => {};
  }

  async status() {
    return MOCK_STATUS;
  }
}
