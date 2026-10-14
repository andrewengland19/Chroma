import type { IpcClient } from "./types";

export type ArtResolver = (url: string | null) => string | null;

export interface Backend {
  ipc: IpcClient;
  artSrc: ArtResolver;
  freeze: boolean; // ?freeze: stop local position interpolation (deterministic screenshots)
  mock: boolean;
  dispose?: () => void;
}

/** `npm run dev:mock` (VITE_IPC=mock) or `?mock=<scenario>` → in-browser engine; otherwise Tauri. */
export async function createBackend(): Promise<Backend> {
  const params = new URLSearchParams(window.location.search);
  const freeze = params.has("freeze");
  const inTauri = "__TAURI_INTERNALS__" in window;
  // DEV-only branch: production bundles drop it (and the mock engine) entirely.
  if (import.meta.env.DEV && (import.meta.env.VITE_IPC === "mock" || params.has("mock") || !inTauri)) {
    const { MockEngine } = await import("../mock/engine");
    const scenario = (params.get("mock") || "live") as "mockup-console" | "mockup-setup" | "live" | "empty";
    if (!inTauri && !params.has("mock") && import.meta.env.VITE_IPC !== "mock") {
      console.info("CHROMA: not running inside Tauri — using the mock engine (?mock=live)");
    }
    const engine = new MockEngine(scenario);
    return { ipc: engine, artSrc: (u) => engine.artSrc(u), freeze: freeze || scenario.startsWith("mockup-"), mock: true,
      dispose: () => engine.dispose() };
  }
  const { tauriIpc } = await import("./tauri");
  return { ipc: tauriIpc, artSrc: (u) => u, freeze, mock: false };
}
