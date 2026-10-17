import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { renderApp } from "../test/helpers";

afterEach(() => vi.useRealTimers());

const intents = (engine: { requests: Record<string, unknown>[] }) =>
  engine.requests.filter((r) => r.op === "intent").map((r) => r.intent);

function sizeTrack(el: HTMLElement, top = 100, height = 246) {
  el.getBoundingClientRect = () =>
    ({ top, bottom: top + height, height, left: 0, right: 36, width: 36, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
}

describe("Fader bank (gui-1)", () => {
  test("shows the current bank's 8 strips with group names, values and live colours", async () => {
    await renderApp();
    expect(within(screen.getByTestId("bank")).getByRole("radio", { name: "A" })).toHaveAttribute("aria-checked", "true");
    expect(within(screen.getByTestId("fader-fn")).getByRole("radio", { name: "LEVEL" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("scribble-1")).toHaveTextContent("LR ALL");
    expect(screen.getByTestId("scribble-8")).toHaveTextContent("FX");
    expect(screen.getByTestId("value-1")).toHaveTextContent("82%");
    expect(screen.getByTestId("value-3")).toHaveTextContent("55%");
    expect(screen.getByTestId("value-master")).toHaveTextContent("90%");
    expect(screen.getByTestId("fader-1")).toHaveAttribute("aria-valuenow", "82");
    // fill = the channel's live colour (X5): LR ALL is red, LR R is blue
    const red = screen.getByTestId("fader-1-fill").style.background;
    const blue = screen.getByTestId("fader-6-fill").style.background;
    expect(red).toMatch(/rgb\((\d+), (\d+), (\d+)\)/);
    const [r1, , b1] = red.match(/\d+/g)!.map(Number);
    const [r6, , b6] = blue.match(/\d+/g)!.map(Number);
    expect(r1 > b1 && b6 > r6).toBe(true);
    expect(screen.getByTestId("flash-3")).toHaveAttribute("aria-pressed", "true");   // mockup: C3 flashing
  });

  test("dragging a fader sends fader intents and the strip follows the engine", async () => {
    const { engine } = await renderApp();
    const f = screen.getByTestId("fader-2");
    sizeTrack(f);
    await act(async () => {
      fireEvent.pointerDown(f, { clientY: 100 + 246 * 0.5, buttons: 1 });
      fireEvent.pointerMove(f, { clientY: 100 + 246 * 0.25, buttons: 1 });
    });
    expect(intents(engine)).toContainEqual({ intent: "fader", ch: 2, v: 0.5 });
    expect(intents(engine)).toContainEqual({ intent: "fader", ch: 2, v: 0.75 });
    expect(screen.getByTestId("value-2")).toHaveTextContent("75%");
    const m = screen.getByTestId("fader-master");
    sizeTrack(m);
    await act(async () => fireEvent.pointerDown(m, { clientY: 100 + 246, buttons: 1 }));
    expect(intents(engine)).toContainEqual({ intent: "fader", ch: "master", v: 0 });
    expect(screen.getByTestId("value-master")).toHaveTextContent("0%");
  });

  test("FLASH is momentary: down on press, up on release", async () => {
    const { engine } = await renderApp();
    const b = screen.getByTestId("flash-1");
    await act(async () => fireEvent.pointerDown(b, { button: 0 }));
    expect(intents(engine).at(-1)).toEqual({ intent: "flash", ch: 1, down: true });
    expect(b).toHaveAttribute("aria-pressed", "true");
    await act(async () => fireEvent.pointerUp(b));
    expect(intents(engine).at(-1)).toEqual({ intent: "flash", ch: 1, down: false });
    expect(b).toHaveAttribute("aria-pressed", "false");
    await act(async () => fireEvent.pointerUp(b));          // a stray release sends nothing
    expect(intents(engine).filter((i) => (i as { intent: string }).intent === "flash")).toHaveLength(2);
  });

  test("a FLASH held while the bank changes is released, not left stuck on", async () => {
    const { engine } = await renderApp();
    await act(async () => fireEvent.pointerDown(screen.getByTestId("flash-2"), { button: 0 }));
    await act(async () => fireEvent.click(screen.getByTestId("bank-B")));
    expect(intents(engine).filter((i) => (i as { intent: string }).intent === "flash"))
      .toEqual([{ intent: "flash", ch: 2, down: true }, { intent: "flash", ch: 2, down: false }]);
  });

  test("the ASSIGN button closes its own picker", async () => {
    await renderApp();
    const b = screen.getByTestId("assign-4");
    await act(async () => fireEvent.click(b));
    expect(screen.getByTestId("assign-picker-4")).toBeInTheDocument();
    await act(async () => {
      fireEvent.pointerDown(b);
      fireEvent.click(b);
    });
    expect(screen.queryByTestId("assign-picker-4")).toBeNull();
  });

  test("bank tabs and FADER FUNCTION switch the strips; ghost markers show disengaged faders", async () => {
    const { engine } = await renderApp();
    expect(screen.getByTestId("fader-1-cap")).not.toHaveAttribute("data-ghost");
    await act(async () => fireEvent.click(screen.getByTestId("bank-B")));
    expect(intents(engine)).toContainEqual({ intent: "bank", bank: "B" });
    expect(screen.getByTestId("scribble-1")).toHaveTextContent("TV");
    expect(screen.getByTestId("fader-1-cap")).toHaveAttribute("data-ghost", "true");
    expect(screen.getByTestId("fader-1-cap")).toHaveAttribute("title", expect.stringContaining("not picked up"));
    await act(async () => fireEvent.click(screen.getByTestId("bank-A")));
    await act(async () => fireEvent.click(screen.getByTestId("fn-hue")));
    expect(intents(engine)).toContainEqual({ intent: "fader_fn", fn: "hue" });
    expect(screen.getByTestId("value-6")).toHaveTextContent("222°");
    expect(screen.getByTestId("fader-6")).toHaveAttribute("aria-valuenow", String(Math.round((222 / 360) * 100)));
    expect(screen.getByTestId("strip-1")).toHaveTextContent("360");
  });

  test("ASSIGN selects the channel and opens a group picker", async () => {
    const { engine } = await renderApp();
    await act(async () => fireEvent.click(screen.getByTestId("assign-4")));
    expect(intents(engine)).toContainEqual({ intent: "assign", ch: 4 });
    const picker = screen.getByTestId("assign-picker-4");
    expect(within(picker).getByRole("option", { name: "PLAYBILL" })).toHaveAttribute("aria-selected", "true");
    await act(async () => fireEvent.click(within(picker).getByRole("option", { name: "LAMP" })));
    expect(intents(engine)).toContainEqual({ intent: "assign", ch: 4, group_id: "lamp" });
    expect(screen.queryByTestId("assign-picker-4")).toBeNull();
    expect(screen.getByTestId("scribble-4")).toHaveTextContent("LAMP");
    await act(async () => fireEvent.click(screen.getByTestId("assign-4")));
    await act(async () => fireEvent.click(within(screen.getByTestId("assign-picker-4")).getByRole("option", { name: "None" })));
    expect(intents(engine)).toContainEqual({ intent: "assign", ch: 4, group_id: null });
    expect(screen.getByTestId("scribble-4")).toHaveTextContent("—");
  });

  test("both BLACKOUT buttons toggle blackout and show it", async () => {
    const { engine } = await renderApp();
    await act(async () => fireEvent.click(screen.getByTestId("btn-blackout")));
    expect(intents(engine)).toContainEqual({ intent: "blackout", on: true });
    expect(screen.getByTestId("blackout-master")).toHaveAttribute("aria-pressed", "true");
    await act(async () => fireEvent.click(screen.getByTestId("blackout-master")));
    expect(intents(engine)).toContainEqual({ intent: "blackout", on: false });
    expect(screen.getByTestId("btn-blackout")).toHaveAttribute("aria-pressed", "false");
  });
});

describe("COLOR and CUES (gui-1)", () => {
  test("knobs show the last-touched channel and send encoder detents", async () => {
    const { engine } = await renderApp();
    expect(screen.getByTestId("knob-hue-value")).toHaveTextContent("222°");     // LR R
    expect(screen.getByTestId("knob-sat-value")).toHaveTextContent("78%");
    expect(screen.getByTestId("swatch-5")).toHaveAttribute("aria-pressed", "true");   // #3b82f6 ~ 222°
    const k = screen.getByTestId("knob-hue");
    await act(async () => {
      fireEvent.pointerDown(k, { clientY: 200, buttons: 1 });
      fireEvent.pointerMove(k, { clientY: 192, buttons: 1 });                  // 8 px up = 2 detents
    });
    expect(intents(engine)).toContainEqual({ intent: "hue", delta: 2 });
    expect(screen.getByTestId("knob-hue-value")).toHaveTextContent("232°");
    await act(async () => fireEvent.wheel(screen.getByTestId("knob-sat"), { deltaY: 100 }));
    expect(intents(engine)).toContainEqual({ intent: "sat", delta: -1 });
    expect(screen.getByTestId("knob-sat-value")).toHaveTextContent("76%");
  });

  test("palette: tap applies, hold stores", async () => {
    vi.useFakeTimers();
    const { engine } = await renderApp();
    const sw = screen.getByTestId("swatch-0");
    await act(async () => {
      fireEvent.pointerDown(sw, { button: 0 });
      fireEvent.pointerUp(sw);
    });
    expect(intents(engine).at(-1)).toEqual({ intent: "palette_apply", i: 0 });
    await act(async () => {
      fireEvent.pointerDown(screen.getByTestId("swatch-7"), { button: 0 });
      vi.advanceTimersByTime(650);
      fireEvent.pointerUp(screen.getByTestId("swatch-7"));
    });
    expect(intents(engine).at(-1)).toEqual({ intent: "palette_store", i: 7 });
    expect(intents(engine).filter((i) => (i as { intent: string }).intent === "palette_apply")).toHaveLength(1);
  });

  test("cues: tap recalls, shift + tap stores, the active cue is lit", async () => {
    const { engine } = await renderApp();
    expect(screen.getByTestId("cue-2")).toHaveAttribute("aria-pressed", "true");    // mockup: DROP
    expect(screen.getByTestId("cue-2")).toHaveTextContent("DROP");
    await act(async () => {
      fireEvent.pointerDown(screen.getByTestId("cue-4"), { button: 0 });
      fireEvent.pointerUp(screen.getByTestId("cue-4"));
    });
    expect(intents(engine).at(-1)).toEqual({ intent: "cue_recall", i: 4 });
    expect(screen.getByTestId("cue-4")).toHaveAttribute("aria-pressed", "true");
    await act(async () => fireEvent.pointerDown(screen.getByTestId("cue-6"), { button: 0, shiftKey: true }));
    expect(intents(engine).at(-1)).toEqual({ intent: "cue_store", i: 6 });
  });

  test("knobs do nothing without a touched channel; wheel needs a real delta", async () => {
    const { engine } = await renderApp();
    const k = screen.getByTestId("knob-hue");
    await act(async () => fireEvent.wheel(k, { deltaY: 0, deltaX: 30 }));      // horizontal swipe
    await act(async () => fireEvent.wheel(k, { deltaY: 10 }));                 // small trackpad step
    expect(intents(engine).filter((i) => (i as { intent: string }).intent === "hue")).toHaveLength(0);
    await act(async () => fireEvent.wheel(k, { deltaY: 35 }));                 // 45 px in total: one detent
    expect(intents(engine).at(-1)).toEqual({ intent: "hue", delta: -1 });
    act(() => engine.patch({ last_touched: null }));
    expect(screen.getByTestId("knob-hue-value")).toHaveTextContent("—");
    const n = engine.requests.length;
    await act(async () => fireEvent.wheel(k, { deltaY: -100 }));
    expect(engine.requests.length).toBe(n);
  });

  test("an engine error from an intent is shown as a toast", async () => {
    const { engine } = await renderApp();
    act(() => engine.patch({ last_touched: null }));
    await act(async () => {
      fireEvent.pointerDown(screen.getByTestId("swatch-0"), { button: 0 });
      fireEvent.pointerUp(screen.getByTestId("swatch-0"));
    });
    expect(screen.getByRole("status")).toHaveTextContent("touch a channel first");
  });
});

describe("Cues pane: Scenes mode", () => {
  test("toggle swaps the cue pads for saved scenes; tapping one recalls it", async () => {
    const { engine } = await renderApp();
    expect(screen.getByTestId("cue-0")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("tab", { name: "Scenes" })));
    await waitFor(() => expect(screen.getByTestId("scene-0")).toBeInTheDocument());
    expect(screen.queryByTestId("cue-0")).toBeNull();
    expect(screen.getByTestId("scene-0")).toHaveTextContent("sunset");
    await act(async () => fireEvent.click(screen.getByTestId("scene-1")));
    expect(engine.requests).toContainEqual({ op: "scenes.recall", name: "agent.movie_night" });
    expect(screen.getByTestId("scene-1")).toHaveAttribute("aria-pressed", "true");
    await act(async () => fireEvent.click(screen.getByRole("tab", { name: "Cues" })));
    expect(screen.getByTestId("cue-0")).toBeInTheDocument();
  });

  test("with more scenes than pads, double-click opens the picker and hiding one brings in the next", async () => {
    await renderApp();
    await act(async () => fireEvent.click(screen.getByRole("tab", { name: "Scenes" })));
    await waitFor(() => expect(screen.getByTestId("scene-7")).toBeInTheDocument());
    expect(screen.queryByTestId("scene-8")).toBeNull();
    await act(async () => fireEvent.doubleClick(screen.getByTestId("scene-0")));
    expect(screen.queryByTestId("scenes-modal")).toBeNull();            // a pad double-click isn't "configure"
    await act(async () => fireEvent.doubleClick(screen.getByText("Scenes", { selector: "span" })));
    const modal = screen.getByTestId("scenes-modal");
    expect(within(modal).getByLabelText("Show agent.cozy")).toBeDisabled();     // 8 of 8
    await act(async () => fireEvent.click(within(modal).getByLabelText("Show agent.sunset")));
    await act(async () => fireEvent.click(within(modal).getByLabelText("Show agent.cozy")));
    await act(async () => fireEvent.keyDown(window, { key: "Escape" }));
    expect(screen.queryByTestId("scenes-modal")).toBeNull();
    expect(screen.getByTestId("scene-0")).toHaveTextContent("movie_night");
    expect(screen.getByTestId("scene-7")).toHaveTextContent("cozy");
  });
});
