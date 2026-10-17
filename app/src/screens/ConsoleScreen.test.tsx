import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { renderApp } from "../test/helpers";

afterEach(() => vi.useRealTimers());

describe("Console header (gui-1)", () => {
  test("shows what is playing, with progress and time", async () => {
    await renderApp();
    const np = screen.getByTestId("now-playing");
    expect(within(np).getByText("Older")).toBeInTheDocument();
    expect(np).toHaveTextContent("Sasha Alex Sloan·Loser - EP");
    expect(screen.getByTestId("time")).toHaveTextContent("1:42 / 3:25");
    expect(screen.getByTestId("progress").style.width).toBe(`${(102000 / 205000) * 100}%`);
    expect(np.querySelector("img")).toHaveAttribute("src", expect.stringContaining("data:image/svg+xml"));
  });

  test("WHO DRIVES and ART MODE reflect the state and send intents/ops", async () => {
    const { engine } = await renderApp();
    const who = screen.getByTestId("who");
    expect(within(who).getByRole("radio", { name: "BOARD" })).toHaveAttribute("aria-checked", "true");
    const art = screen.getByTestId("art-mode");
    expect(within(art).getByRole("radio", { name: "AI" })).toHaveAttribute("aria-checked", "true");

    await act(async () => fireEvent.click(within(who).getByRole("radio", { name: "AUTO" })));
    expect(engine.requests).toContainEqual({ op: "intent", intent: { intent: "who", who: "auto" } });
    expect(within(who).getByRole("radio", { name: "AUTO" })).toHaveAttribute("aria-checked", "true");

    await act(async () => fireEvent.click(within(art).getByRole("radio", { name: "PAINT" })));
    expect(engine.requests).toContainEqual({ op: "set_art_mode", mode: "paint" });
    expect(within(art).getByRole("radio", { name: "PAINT" })).toHaveAttribute("aria-checked", "true");
  });

  test("status chips follow health", async () => {
    const { engine } = await renderApp();
    expect(screen.getByTestId("chip-atv")).toHaveTextContent("Apple TV");
    expect(screen.getByTestId("chip-lifx")).toHaveTextContent("LIFX 5/5");
    expect(screen.getByTestId("chip-board")).toHaveTextContent("Board: virtual");
    act(() => engine.patch({ health: { lifx: { found: 3 }, board: "offline" } }));
    expect(screen.getByTestId("chip-lifx")).toHaveTextContent("LIFX 3/5");
    expect(screen.getByTestId("chip-board")).toHaveTextContent("Board: offline");
  });

  test("room shows each fixture at its position with its colour", async () => {
    await renderApp();
    const canvas = screen.getByTestId("room-canvas");
    expect(within(canvas).getByText("TV Left")).toBeInTheDocument();
    expect(within(canvas).getByText("5 / 6 lights")).toBeInTheDocument();   // + the virtual light
    const dot = within(screen.getByTestId("room-tv-left")).getByTitle("TV Left");
    expect(dot.style.left).toBe(`${0.12 * 300 - 7.5}px`);
  });

  test("nothing playing", async () => {
    await renderApp("empty");
    expect(screen.getByText("Nothing playing")).toBeInTheDocument();
    expect(screen.getByTestId("time")).toHaveTextContent("–:–– / –:––");
    expect(screen.getByText("0 / 6 lights")).toBeInTheDocument();
  });

  test("position interpolates between engine updates while playing", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "performance"] });
    await renderApp("mockup-console", false);
    expect(screen.getByTestId("time")).toHaveTextContent("1:42 / 3:25");
    await act(async () => vi.advanceTimersByTime(3100));
    expect(screen.getByTestId("time")).toHaveTextContent("1:45 / 3:25");
  });

  test("tabs switch screens", async () => {
    await renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Takes" }));
    expect(screen.getByTestId("takes-screen")).toHaveTextContent("Take details");
    fireEvent.click(screen.getByRole("button", { name: "Setup" }));
    expect(screen.getByTestId("setup-screen")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Console" }));
    expect(screen.getByTestId("console-screen")).toBeInTheDocument();
  });
});

describe("Room panel: drag and Spaces", () => {
  const rigLoaded = async () => waitFor(() => expect(screen.getByRole("tablist", { name: "Spaces" })).toBeInTheDocument());
  const canvasAt = () => {
    const canvas = screen.getByTestId("room-canvas");
    canvas.getBoundingClientRect = () => ({ left: 100, top: 50, width: 300, height: 162, right: 400, bottom: 212, x: 100, y: 50, toJSON: () => ({}) });
    return canvas;
  };

  test("dragging a light moves it and saves the new spot", async () => {
    const { engine } = await renderApp();
    await rigLoaded();
    canvasAt();
    const dot = within(screen.getByTestId("room-washer")).getByTitle("Lamp");
    await act(async () => {
      fireEvent.pointerDown(dot, { clientX: 250, clientY: 124, pointerId: 1 });
      fireEvent.pointerMove(dot, { clientX: 280, clientY: 140, pointerId: 1 });
    });
    expect(dot.style.left).toBe(`${180 - 7.5}px`);                    // follows the pointer
    await act(async () => fireEvent.pointerUp(dot, { pointerId: 1 }));
    const req = engine.requests.find((r) => r.op === "fixtures.upsert") as { fixture: { id: string; x: number; y: number; label: string } };
    expect(req.fixture).toMatchObject({ id: "washer", label: "Lamp", x: 0.6, y: 0.556 });
    expect(engine.state.room.find((r) => r.fixture_id === "washer")).toMatchObject({ x: 0.6, y: 0.556 });
    expect(within(screen.getByTestId("room-washer")).getByTitle("Lamp").style.left).toBe(`${180 - 7.5}px`);  // stays put
  });

  test("a click without moving saves nothing", async () => {
    const { engine } = await renderApp();
    await rigLoaded();
    canvasAt();
    const dot = within(screen.getByTestId("room-washer")).getByTitle("Lamp");
    await act(async () => {
      fireEvent.pointerDown(dot, { clientX: 250, clientY: 124, pointerId: 1 });
      fireEvent.pointerMove(dot, { clientX: 251, clientY: 125, pointerId: 1 });
      fireEvent.pointerUp(dot, { pointerId: 1 });
    });
    expect(engine.requests.some((r) => r.op === "fixtures.upsert")).toBe(false);
  });

  test("Space tabs pick which room is shown", async () => {
    await renderApp();
    await rigLoaded();
    const tabs = screen.getByRole("tablist", { name: "Spaces" });
    expect(within(tabs).getByRole("tab", { name: "Living Room" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(within(tabs).getByRole("tab", { name: "Bedroom" }));
    const canvas = screen.getByTestId("room-canvas");
    expect(within(canvas).queryByText("TV Left")).toBeNull();
    expect(screen.getByTestId("room-bedroom-1")).toBeInTheDocument();       // discovered, not in the rig yet: dim
    expect(within(canvas).getByText("0 / 4 lights")).toBeInTheDocument();
  });

  test("double-click opens the Spaces setup; edits are ops", async () => {
    const { engine } = await renderApp();
    await rigLoaded();
    fireEvent.doubleClick(screen.getByTestId("room-header"));
    const dlg = screen.getByRole("dialog", { name: "Spaces" });
    expect(within(dlg).getByTestId("space-living-room")).toHaveTextContent("default");

    const name = within(dlg).getByLabelText("Name of Bedroom");
    fireEvent.change(name, { target: { value: "Main bedroom" } });
    await act(async () => fireEvent.blur(name));
    expect(engine.requests).toContainEqual({ op: "spaces.upsert", space: { id: "bedroom", name: "Main bedroom" } });

    fireEvent.change(within(dlg).getByLabelText("New space name"), { target: { value: "Kitchen" } });
    await act(async () => fireEvent.click(within(dlg).getByRole("button", { name: "+ Add" })));
    expect(engine.requests).toContainEqual({ op: "spaces.upsert", space: { name: "Kitchen" } });
    await waitFor(() => expect(within(dlg).getByTestId("space-kitchen")).toBeInTheDocument());

    await act(async () => fireEvent.change(within(dlg).getByLabelText("Space for Lamp"), { target: { value: "kitchen" } }));
    expect(engine.rig.rig.fixtures.find((f) => f.id === "washer")?.space).toBe("kitchen");

    await act(async () => fireEvent.click(within(dlg).getByRole("button", { name: "Remove Kitchen" })));
    expect(engine.rig.rig.fixtures.find((f) => f.id === "washer")?.space).toBe("living-room");
    await waitFor(() => expect(within(dlg).queryByTestId("space-kitchen")).toBeNull());

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Spaces" })).toBeNull();
  });

  test("double-clicking a light doesn't open the setup", async () => {
    await renderApp();
    await rigLoaded();
    fireEvent.doubleClick(within(screen.getByTestId("room-washer")).getByTitle("Lamp"));
    expect(screen.queryByRole("dialog", { name: "Spaces" })).toBeNull();
    fireEvent.doubleClick(screen.getByTestId("room-canvas"));
    expect(screen.getByRole("dialog", { name: "Spaces" })).toBeInTheDocument();
  });
});
