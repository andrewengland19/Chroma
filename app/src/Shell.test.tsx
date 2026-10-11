import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { EngineStatus, IpcClient } from "./ipc/types";
import { Shell } from "./Shell";
import { ChromaStore, StoreContext } from "./store";

function withStatus(status: EngineStatus) {
  const ipc: IpcClient = {
    request: async () => ({ ok: false, error: "engine not connected" }),
    onEvent: () => () => {},
    onStatus: () => () => {},
    status: async () => status,
  };
  const store = new ChromaStore(ipc);
  store.start();
  return store;
}

test("shows the engine connection state, and 'fake devices' in fake mode", async () => {
  const store = withStatus({ phase: "connecting", connected: false, supervised: false, pid: null, fake: false, message: "", socket: "" });
  render(<StoreContext.Provider value={store}><Shell /></StoreContext.Provider>);
  expect(await screen.findByTestId("engine-notice")).toHaveTextContent("connecting to engine…");
  expect(screen.getByText("Nothing playing")).toBeInTheDocument();
});

test("fake engine is labelled", async () => {
  const store = withStatus({ phase: "connected", connected: true, supervised: true, pid: 9, fake: true, message: "", socket: "" });
  render(<StoreContext.Provider value={store}><Shell /></StoreContext.Provider>);
  expect(await screen.findByTestId("engine-notice")).toHaveTextContent("fake devices");
});
