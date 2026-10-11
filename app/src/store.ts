// The UI's single source of truth: the engine State, kept current by `state`
// events and merge `patch`es, plus the engine connection status and toasts.
// The UI never changes state locally — it sends intents/ops and waits for patches.
import { createContext, useContext, useEffect, useState, useSyncExternalStore } from "react";
import type { Intent, IpcEvent, State } from "./contracts";
import type { ArtResolver } from "./ipc";
import type { EngineStatus, IpcClient, Reply } from "./ipc/types";
import { applyMergePatch } from "./lib/mergePatch";

export interface Toast {
  id: number;
  level: "info" | "warn" | "error";
  text: string;
}

export interface Snapshot {
  state: State | null;
  status: EngineStatus | null;
  posAt: number; // performance.now() when now.pos_ms was last received
  toasts: Toast[];
}

export class ChromaStore {
  private snap: Snapshot = { state: null, status: null, posAt: 0, toasts: [] };
  private subs = new Set<() => void>();
  private toastId = 1;

  constructor(
    readonly ipc: IpcClient,
    readonly artSrc: ArtResolver = (u) => u,
    readonly freeze = false,
    private clock: () => number = () => performance.now(),
  ) {}

  start(): () => void {
    const offEv = this.ipc.onEvent((e) => this.onEvent(e));
    const offSt = this.ipc.onStatus((s) => this.onStatus(s));
    void this.ipc.status().then((s) => this.onStatus(s)).catch(() => {});
    return () => {
      offEv();
      offSt();
    };
  }

  subscribe = (fn: () => void) => {
    this.subs.add(fn);
    return () => this.subs.delete(fn);
  };

  getSnapshot = () => this.snap;

  private set(p: Partial<Snapshot>) {
    this.snap = { ...this.snap, ...p };
    this.subs.forEach((f) => f());
  }

  private onStatus(s: EngineStatus) {
    const wasConnected = this.snap.status?.connected;
    this.set({ status: s });
    if (s.connected && !wasConnected) void this.refreshState();
  }

  async refreshState() {
    const r = await this.ipc.request({ op: "get_state" });
    if (r.ok && r.state) this.set({ state: r.state as State, posAt: this.clock() });
  }

  onEvent(e: IpcEvent) {
    if (e.ev === "state") {
      this.set({ state: e.state, posAt: this.clock() });
    } else if (e.ev === "patch") {
      if (!this.snap.state) {
        void this.refreshState();
        return;
      }
      const state = applyMergePatch(this.snap.state, e.patch);
      const now = (e.patch as { now?: Record<string, unknown> }).now;
      const posMoved = now && ("pos_ms" in now || "track_id" in now || "playing" in now);
      this.set({ state, posAt: posMoved ? this.clock() : this.snap.posAt });
    } else if (e.ev === "toast") {
      this.toast(e.level, e.text);
    }
  }

  toast(level: Toast["level"], text: string) {
    const t = { id: this.toastId++, level, text };
    this.set({ toasts: [...this.snap.toasts, t].slice(-4) });
    setTimeout(() => this.set({ toasts: this.snap.toasts.filter((x) => x.id !== t.id) }), 4000);
  }

  async op(op: string, args: Record<string, unknown> = {}): Promise<Reply> {
    const r = await this.ipc.request({ op, ...args });
    if (!r.ok) this.toast("error", r.error || `${op} failed`);
    return r;
  }

  intent(intent: Intent): Promise<Reply> {
    return this.op("intent", { intent });
  }
}

export const StoreContext = createContext<ChromaStore | null>(null);

export function useStore(): ChromaStore {
  const s = useContext(StoreContext);
  if (!s) throw new Error("StoreContext missing");
  return s;
}

export function useSnapshot(): Snapshot {
  const s = useStore();
  return useSyncExternalStore(s.subscribe, s.getSnapshot);
}

/** Subscribe to one slice. Untouched parts of the state keep their identity across
 *  merge patches, so a component re-renders only when its slice changes. */
export function useSelect<T>(sel: (s: Snapshot) => T): T {
  const s = useStore();
  return useSyncExternalStore(s.subscribe, () => sel(s.getSnapshot()));
}

/** Song position interpolated locally between engine updates (<= 4 Hz). */
export function useSongPosition(): number | null {
  const store = useStore();
  const now = useSelect((s) => s.state?.now);
  const posAt = useSelect((s) => s.posAt);
  const [, setTick] = useState(0);
  const playing = !!now?.playing && !store.freeze;
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setTick((t) => t + 1), 100);
    return () => clearInterval(id);
  }, [playing]);
  const pos = now?.pos_ms;
  if (pos == null) return null;
  if (!playing) return pos;
  const dur = now?.dur_ms ?? Infinity;
  return Math.min(dur, pos + (performance.now() - posAt));
}
