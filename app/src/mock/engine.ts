// In-browser stand-in for chromad, so the UI runs (and can be screenshotted) without
// Tauri. Scenarios:
//   mockup-console   the exact data of docs/mvp/gui-1-console.png (static)
//   live             same data, playing: position advances, controls respond
//   empty            engine up, nothing playing
// Real engine semantics live in hub/ (tested with pytest); this only mirrors enough
// of them for the UI to be exercised by hand and by component tests.
import mockupState from "../../../contracts/examples/state/ok-mockup-console.json";
import type { IpcEvent, State } from "../contracts";
import { applyMergePatch } from "../lib/mergePatch";
import type { EngineStatus, IpcClient, Reply } from "../ipc/types";
import { MOCK_ART } from "./art";
import { MockRig } from "./rig";

export const MOCK_STATUS: EngineStatus = {
  phase: "connected",
  connected: true,
  supervised: false,
  pid: null,
  fake: true,
  message: "mock engine (browser)",
  socket: "mock",
};

export type Scenario = "mockup-console" | "mockup-setup" | "live" | "empty";

function baseState(scenario: Scenario): State {
  const s = structuredClone(mockupState) as State;
  if (scenario === "empty") {
    s.now = { track_id: "", title: "", artist: "", album: "", playing: false, pos_ms: null, dur_ms: null, art_url: null };
    s.rec = { state: "idle", take_id: null, elapsed_ms: 0, events: 0, mode: "replace", name: "", groups_touched: 0 };
    s.room = s.room.map((r) => ({ ...r, hex: null }));
  }
  if (scenario === "live") {
    s.rec = { state: "idle", take_id: null, elapsed_ms: 0, events: 0, mode: "replace", name: "", groups_touched: 0 };
  }
  return s;
}

export class MockEngine implements IpcClient {
  state: State;
  private events = new Set<(e: IpcEvent) => void>();
  private timer: ReturnType<typeof setInterval> | null = null;
  readonly requests: Record<string, unknown>[] = [];
  readonly rig = new MockRig((rev) => this.patch({ fixtures_rev: rev }));

  constructor(readonly scenario: Scenario = "live") {
    this.state = baseState(scenario);
    this.state.fixtures_rev = this.rig.rig.rev;
    if (scenario === "live") this.timer = setInterval(() => this.tick(250), 250);
  }

  dispose() {
    if (this.timer) clearInterval(this.timer);
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
        return { ok: false, error: `mock: intent ${String(i?.intent)} not handled yet` };
      }
      default:
        if (typeof req.op === "string" && /^(fixtures|groups|banks)\./.test(req.op)) return this.rig.handle(req);
        return { ok: false, error: `unknown op '${String(req.op)}'` };
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
