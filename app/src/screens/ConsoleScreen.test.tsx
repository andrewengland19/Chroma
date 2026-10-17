import { act, fireEvent, screen, within } from "@testing-library/react";
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
    expect(within(canvas).getByText("5 / 5 lights")).toBeInTheDocument();
    const dot = within(screen.getByTestId("room-tv-left")).getByTitle("TV Left");
    expect(dot.style.left).toBe(`${0.12 * 300 - 7.5}px`);
  });

  test("nothing playing", async () => {
    await renderApp("empty");
    expect(screen.getByText("Nothing playing")).toBeInTheDocument();
    expect(screen.getByTestId("time")).toHaveTextContent("–:–– / –:––");
    expect(screen.getByText("0 / 5 lights")).toBeInTheDocument();
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
