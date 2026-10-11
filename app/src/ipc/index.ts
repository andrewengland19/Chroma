import type { IpcClient } from "./types";

export type ArtResolver = (url: string | null) => string | null;

export interface Backend {
  ipc: IpcClient;
  artSrc: ArtResolver;
  freeze: boolean; // ?freeze: stop local position interpolation (deterministic screenshots)
  mock: boolean;
}

/** `npm run dev:mock` (VITE_IPC=mock) or `?mock=<scenario>` → in-browser engine; otherwise Tauri. */
export async function createBackend(): Promise<Backend> {
  const params = new URLSearchParams(window.location.search);
  const freeze = params.has("freeze");
  if (import.meta.env.VITE_IPC === "mock" || params.has("mock")) {
    const { MockEngine } = await import("../mock/engine");
    const scenario = (params.get("mock") || "live") as "mockup-console" | "live" | "empty";
    const engine = new MockEngine(scenario);
    return { ipc: engine, artSrc: (u) => engine.artSrc(u), freeze: freeze || scenario === "mockup-console", mock: true };
  }
  const { tauriIpc } = await import("./tauri");
  return { ipc: tauriIpc, artSrc: (u) => u, freeze, mock: false };
}
