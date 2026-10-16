import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { takeLanes, laneSubtitle, clockMs, nudgeText, takeForTrack } from "../lib/takes";
import { mockTakes } from "../mock/takes";
import { renderApp } from "../test/helpers";

afterEach(() => vi.restoreAllMocks());

const ops = (engine: { requests: Record<string, unknown>[] }, op: string) => engine.requests.filter((r) => r.op === op);

async function openTakes(scenario: Parameters<typeof renderApp>[0] = "mockup-takes") {
  const r = await renderApp(scenario);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Takes" })));
  await waitFor(() => expect(screen.getByTestId("take-name")).toBeInTheDocument());
  return r;
}

describe("take helpers", () => {
  test("lanes: level curve from init + level events, flash blocks, kinds", () => {
    const { header, events } = mockTakes()[0];
    const lanes = takeLanes(header, events);
    expect(lanes.map((l) => l.name)).toEqual(["LR ALL", "BED", "COUNTER", "PLAYBILL", "LR L", "LR R", "ACCENT", "FX"]);
    const counter = lanes[2];
    expect(counter.flashes).toHaveLength(7);
    expect(counter.flashes[0]).toEqual([72000, 73500]);
    expect(counter.curve[0]).toEqual([0, 0.22]);
    expect(counter.curve[counter.curve.length - 1][0]).toBe(205000);
    expect(laneSubtitle(counter)).toBe("level · color");
    expect(lanes[1].events).toBe(0);
    expect(laneSubtitle({ ...counter, kinds: { level: false, color: false, flash: true } })).toBe("flash only");
  });

  test("formatting and the Console PLAY take", () => {
    expect(clockMs(102310)).toBe("01:42.310");
    expect(nudgeText(12)).toBe("+ 12 ms");
    expect(nudgeText(-5)).toBe("− 5 ms");
    const sums = mockTakes().map((t) => ({ ...t.header, created_ms: 0, events: 0, groups_used: [], art_hex: null }));
    expect(takeForTrack(sums, { track_id: "a1b2c3d4e5f60718", title: "", artist: "" } as never)?.title).toBe("Older");
    expect(takeForTrack(sums, { track_id: "x", title: "Nope", artist: "" } as never)).toBeUndefined();
  });
});

describe("Takes screen (gui-2)", () => {
  test("library, lanes, markers, transport state and details", async () => {
    await openTakes();
    const lib = screen.getAllByTestId(/^take-2026/);
    expect(lib).toHaveLength(6);
    expect(lib[0]).toHaveTextContent("Older");
    expect(lib[0]).toHaveTextContent("3:25");
    expect(lib[0]).toHaveTextContent("6 groups");
    expect(lib[0]).toHaveAttribute("aria-current", "true");
    expect(within(lib[0]).getByTestId("take-active")).toBeInTheDocument();     // playing
    expect(screen.getByTestId("takes-footer")).toHaveTextContent("6 takes");
    await waitFor(() => expect(screen.getByTestId("lane-counter")).toBeInTheDocument());
    expect(screen.getByTestId("lane-bed")).toHaveTextContent("no events in this take");
    expect(screen.getByTestId("marker-DROP")).toBeInTheDocument();
    expect(screen.getByTestId("take-time")).toHaveTextContent("01:42.310 / 03:25");
    expect(screen.getByTestId("nudge")).toHaveTextContent("+ 12 ms");
    expect(screen.getByTestId("btn-play")).toHaveAttribute("aria-pressed", "true");
    expect(within(screen.getByTestId("rec-mode")).getByRole("radio", { name: "MERGE" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("take-name")).toHaveTextContent("Older — draft 03");
    expect(screen.getByTestId("stat-events")).toHaveTextContent("412");
    expect(screen.getByTestId("stat-sync")).toHaveTextContent("±8 ms");
    expect(screen.getByTestId("training")).toHaveAttribute("aria-checked", "true");
  });

  test("search filters, selecting loads another take", async () => {
    const { engine } = await openTakes();
    fireEvent.change(screen.getByTestId("takes-search"), { target: { value: "justice" } });
    expect(screen.getAllByTestId(/^take-2026/)).toHaveLength(1);
    await act(async () => fireEvent.click(screen.getByTestId("take-20260925-200000")));
    await waitFor(() => expect(screen.getByTestId("take-name")).toHaveTextContent("Phantom"));
    expect(ops(engine, "takes.get").at(-1)).toMatchObject({ take_id: "20260925-200000" });
  });

  test("REC records into the selected take with the mode; STOP stops; nudge; loop", async () => {
    const { engine } = await openTakes();
    await act(async () => fireEvent.click(within(screen.getByTestId("rec-mode")).getByRole("radio", { name: "OVERDUB" })));
    await act(async () => fireEvent.click(screen.getByTestId("btn-rec")));
    expect(ops(engine, "rec.arm").at(-1)).toMatchObject({ take_id: "20261009-214800", mode: "overdub" });
    expect(screen.getByTestId("btn-rec")).toHaveAttribute("aria-pressed", "true");
    await act(async () => fireEvent.click(screen.getByTestId("btn-stop")));
    expect(ops(engine, "rec.stop")).toHaveLength(1);
    expect(ops(engine, "play.stop")).toHaveLength(1);
    await act(async () => fireEvent.click(screen.getByTestId("nudge-plus")));
    expect(ops(engine, "take.nudge").at(-1)).toMatchObject({ ms: 17 });
    await act(async () => fireEvent.click(screen.getByTestId("nudge-minus"), { shiftKey: true }));
    await waitFor(() => expect(screen.getByTestId("nudge")).toHaveTextContent("+ 16 ms"));
    await act(async () => fireEvent.click(screen.getByTestId("loop")));
    await act(async () => fireEvent.click(screen.getByTestId("btn-play")));
    expect(ops(engine, "play.start").at(-1)).toMatchObject({ take_id: "20261009-214800", loop: true });
  });

  test("markers: M adds at the playhead, a flag click removes", async () => {
    const { engine } = await openTakes();
    await act(async () => fireEvent.keyDown(window, { key: "m" }));
    const input = screen.getByTestId("marker-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "CHORUS" } });
    await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
    expect(ops(engine, "takes.marker.add").at(-1)).toMatchObject({ p_ms: 102310, label: "CHORUS" });
    await waitFor(() => expect(screen.getByTestId("marker-CHORUS")).toBeInTheDocument());
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await act(async () => fireEvent.click(screen.getByTestId("marker-VERSE")));
    expect(ops(engine, "takes.marker.remove").at(-1)).toMatchObject({ p_ms: 20000 });
  });

  test("rename, training flag, duplicate, delete, export", async () => {
    const { engine } = await openTakes();
    await act(async () => fireEvent.doubleClick(screen.getByTestId("take-name")));
    const input = screen.getByTestId("take-name-input");
    fireEvent.change(input, { target: { value: "Older — final" } });
    await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
    await waitFor(() => expect(screen.getByTestId("take-name")).toHaveTextContent("Older — final"));
    await act(async () => fireEvent.click(screen.getByTestId("training")));
    await waitFor(() => expect(screen.getByTestId("training")).toHaveAttribute("aria-checked", "false"));
    await act(async () => fireEvent.click(screen.getByTestId("take-duplicate")));
    await waitFor(() => expect(screen.getByTestId("take-name")).toHaveTextContent("Older — final copy"));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await act(async () => fireEvent.click(screen.getByTestId("take-delete")));
    expect(ops(engine, "takes.delete").at(-1)).toMatchObject({ take_id: "20261009-214800-1" });
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    await waitFor(() => expect(screen.getByTestId("take-name")).toBeInTheDocument());
    await act(async () => fireEvent.click(screen.getByTestId("take-export")));
    expect(ops(engine, "takes.export")).toHaveLength(1);
  });
});

describe("Console transport + activity strip (gui-1)", () => {
  test("strip shows the recording; REC stops it; PLAY plays the song's newest take", async () => {
    const { engine } = await renderApp("mockup-console");
    expect(screen.getByTestId("activity-title")).toHaveTextContent("REC Older — draft 03");
    expect(screen.getByTestId("activity-sub")).toHaveTextContent("00:01:42 · 412 events · 6 groups touched");
    expect(screen.getByTestId("activity-playhead")).toBeInTheDocument();
    expect(screen.getByTestId("btn-rec")).toHaveAttribute("aria-pressed", "true");
    await act(async () => fireEvent.click(screen.getByTestId("btn-rec")));
    expect(ops(engine, "rec.stop")).toHaveLength(1);
    await waitFor(() => expect(screen.getByTestId("activity-title")).toHaveTextContent("No take running"));
    await act(async () => fireEvent.click(screen.getByTestId("btn-play")));
    expect(ops(engine, "play.start").at(-1)).toMatchObject({ take_id: "20261009-214800" });
    await waitFor(() => expect(screen.getByTestId("activity-title")).toHaveTextContent("PLAY Older — draft 03"));
    await act(async () => fireEvent.click(screen.getByTestId("btn-rec")));
    expect(ops(engine, "rec.arm").at(-1)).toEqual({ op: "rec.arm" });                 // a new take
  });

  test("PLAY with no take of this song warns instead of sending", async () => {
    const { engine, store } = await renderApp("empty");
    await act(async () => fireEvent.click(screen.getByTestId("btn-play")));
    expect(ops(engine, "play.start")).toHaveLength(0);
    expect(store.getSnapshot().toasts.at(-1)?.text).toBe("Nothing is playing");
  });
});
