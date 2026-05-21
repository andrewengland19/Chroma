import { contextBridge, ipcRenderer } from "electron";

export type SidecarEvent =
  | { event: "ready"; config: Record<string, unknown> }
  | { event: "track_change"; artist: string; title: string; album: string; artwork_path: string }
  | { event: "colors_pushed"; colors: string[]; brightness_scales: number[] }
  | { event: "white_skip"; reason: string }
  | { event: "status"; message: string }
  | { event: "error"; message: string }
  | { event: "config_updated"; key: string; value: unknown }
  | { event: "pins_updated"; colors: string[] }
  | { event: "config"; config: Record<string, unknown> };

contextBridge.exposeInMainWorld("chroma", {
  send: (cmd: unknown) => ipcRenderer.invoke("sidecar:send", cmd),
  onEvent: (cb: (e: SidecarEvent) => void) => {
    const listener = (_evt: unknown, payload: SidecarEvent) => cb(payload);
    ipcRenderer.on("sidecar:event", listener);
    return () => ipcRenderer.removeListener("sidecar:event", listener);
  },
  openMusic: () => ipcRenderer.invoke("app:openMusic"),
});
