import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import type { IpcEvent } from "../contracts";
import type { EngineStatus, IpcClient, Reply } from "./types";

function subscribe<T>(name: string, cb: (p: T) => void): () => void {
  let unlisten: (() => void) | null = null;
  let cancelled = false;
  listen<T>(name, (e) => cb(e.payload))
    .then((u) => (cancelled ? u() : (unlisten = u)))
    .catch((err) => console.error(`CHROMA: cannot listen to ${name}`, err));
  return () => {
    cancelled = true;
    unlisten?.();
  };
}

export const tauriIpc: IpcClient = {
  async request(req) {
    try {
      return await invoke<Reply>("chromad_request", { req });
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },
  onEvent: (cb) => subscribe<IpcEvent>("chromad://event", cb),
  onStatus: (cb) => subscribe<EngineStatus>("chromad://status", cb),
  status: () => invoke<EngineStatus>("engine_status"),
  setTitle: (t) => void getCurrentWindow().setTitle(t).catch(() => {}),
};
