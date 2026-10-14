import { describe, expect, test, vi } from "vitest";
import type { IpcEvent } from "./contracts";
import type { EngineStatus, IpcClient } from "./ipc/types";
import { MockEngine } from "./mock/engine";
import { ChromaStore } from "./store";

function fakeIpc(over: Partial<IpcClient> = {}) {
  let ev: ((e: IpcEvent) => void) | null = null;
  let st: ((s: EngineStatus) => void) | null = null;
  const ipc: IpcClient = {
    request: vi.fn(async () => ({ ok: true, state: new MockEngine("mockup-console").state })),
    onEvent: (cb) => ((ev = cb), () => (ev = null)),
    onStatus: (cb) => ((st = cb), () => (st = null)),
    status: async () => ({ phase: "connecting", connected: false, supervised: false, pid: null, fake: false, message: "", socket: "" }),
    ...over,
  };
  return { ipc, emit: (e: IpcEvent) => ev?.(e), status: (s: EngineStatus) => st?.(s) };
}

const CONNECTED: EngineStatus = { phase: "connected", connected: true, supervised: true, pid: 1, fake: true, message: "", socket: "" };

describe("ChromaStore", () => {
  test("fetches state when the engine connects, then applies patches", async () => {
    const f = fakeIpc();
    const s = new ChromaStore(f.ipc);
    s.start();
    f.status(CONNECTED);
    await vi.waitFor(() => expect(s.getSnapshot().state?.now.title).toBe("Older"));
    f.emit({ ev: "patch", patch: { now: { pos_ms: 5000 }, who: "auto" } });
    expect(s.getSnapshot().state?.now.pos_ms).toBe(5000);
    expect(s.getSnapshot().state?.who).toBe("auto");
    expect(s.getSnapshot().state?.now.title).toBe("Older");
  });

  test("a patch before any state triggers a full fetch instead", async () => {
    const f = fakeIpc();
    const s = new ChromaStore(f.ipc);
    s.start();
    f.emit({ ev: "patch", patch: { who: "auto" } });
    expect(f.ipc.request).toHaveBeenCalledWith({ op: "get_state" });
    await vi.waitFor(() => expect(s.getSnapshot().state).not.toBeNull());
  });

  test("toasts from the engine and from failed ops", async () => {
    vi.useFakeTimers();
    const f = fakeIpc({ request: vi.fn(async () => ({ ok: false, error: "unknown op 'x'" })) });
    const s = new ChromaStore(f.ipc);
    s.start();
    f.emit({ ev: "toast", level: "warn", text: "Nothing is playing" });
    await s.op("x");
    expect(s.getSnapshot().toasts.map((t) => t.text)).toEqual(["Nothing is playing", "unknown op 'x'"]);
    vi.advanceTimersByTime(4100);
    expect(s.getSnapshot().toasts).toEqual([]);
    vi.useRealTimers();
  });
});

test("the rig is fetched once per fixtures_rev, even if the reply's rev disagrees", async () => {
  const f = fakeIpc();
  const s = new ChromaStore(f.ipc);
  s.start();
  f.status(CONNECTED);
  await vi.waitFor(() => expect(s.getSnapshot().rig).not.toBeNull());
  const calls = () => (f.ipc.request as ReturnType<typeof vi.fn>).mock.calls.filter((c) => c[0].op === "fixtures.list").length;
  await new Promise((r) => setTimeout(r, 20));
  expect(calls()).toBe(1);
  f.emit({ ev: "patch", patch: { fixtures_rev: 99 } });
  await vi.waitFor(() => expect(calls()).toBe(2));
  f.emit({ ev: "patch", patch: { who: "auto" } });
  await new Promise((r) => setTimeout(r, 20));
  expect(calls()).toBe(2);
});
