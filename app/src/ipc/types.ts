import type { IpcEvent } from "../contracts";

/** Mirrors the Rust shell's EngineStatus (src-tauri/src/supervisor.rs). */
export interface EngineStatus {
  phase: "connecting" | "connected" | "starting" | "restarting" | "waiting" | "stopped";
  connected: boolean;
  supervised: boolean;
  pid: number | null;
  fake: boolean;
  message: string;
  socket: string;
}

export type Reply = { id?: number | null; ok: boolean; error?: string; [k: string]: unknown };

/** What the UI needs from the engine. Implemented over Tauri (real) or in-browser (mock). */
export interface IpcClient {
  request(req: Record<string, unknown>): Promise<Reply>;
  onEvent(cb: (ev: IpcEvent) => void): () => void;
  onStatus(cb: (s: EngineStatus) => void): () => void;
  status(): Promise<EngineStatus>;
  setTitle?(title: string): void;
}
