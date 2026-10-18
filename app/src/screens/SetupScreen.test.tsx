import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { renderApp } from "../test/helpers";

async function setup() {
  const r = await renderApp("mockup-setup");
  fireEvent.click(screen.getByRole("button", { name: "Setup" }));
  await waitFor(() => expect(screen.getByTestId("row-tv-left")).toBeInTheDocument());
  return r;
}

function dt() {
  const data: Record<string, string> = {};
  return { setData: (k: string, v: string) => (data[k] = v), getData: (k: string) => data[k] ?? "", data };
}

describe("Setup (gui-3)", () => {
  test("fixture table: rows, badges, addresses, classes, groups; series collapse", async () => {
    await setup();
    const tv = screen.getByTestId("row-tv-left");
    expect(tv).toHaveTextContent("TV Left");
    expect(tv).toHaveTextContent("LIFX");
    expect(tv).toHaveTextContent("192.168.0.31");
    expect(tv).toHaveTextContent("slow");
    expect(tv).toHaveTextContent("lr-all · lr-l");
    const bed = screen.getByTestId("row-Bedroom 1–4");
    expect(bed).toHaveTextContent("not added yet");
    expect(bed).toHaveTextContent(/^.*bed$/);                 // only the group all four share
    const pb = screen.getByTestId("row-Playbill 01–12");
    expect(pb).toHaveTextContent("WLED");
    expect(pb).toHaveTextContent("planned");
    expect(pb).toHaveTextContent("fast");
    expect(pb).not.toHaveTextContent("pb-1-4");
    expect(screen.getByTestId("row-virtual-light-1")).toHaveTextContent("in-app");
    const rows = within(screen.getByTestId("fixture-rows")).getAllByText(/./, { selector: "span.font-semibold" });
    expect(rows.map((r) => r.textContent)).toEqual(["TV Left", "TV Right", "Couch L", "Couch R", "Lamp", "Bedroom 1–4",
      "Counter strip", "Playbill 01–12", "Virtual Light 1"]);
  });

  test("Board card: pairing creates the key once and shows it; status follows health", async () => {
    const { engine } = await setup();
    expect(screen.getByTestId("board-status")).toHaveTextContent("virtual · sim-1");   // the mockup scenario
    act(() => engine.patch({ health: { board: "none" }, board_link: { paired: false, id: null } }));
    expect(screen.getByTestId("board-status")).toHaveTextContent("not paired");
    await act(async () => fireEvent.click(screen.getByTestId("board-pair")));
    expect(engine.requests).toContainEqual({ op: "board.pairing" });
    expect(screen.getByTestId("board-key")).toHaveTextContent(/^(ab){32}$/);
    expect(screen.getByTestId("board-pairing")).toHaveTextContent("192.168.0.20:9876");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByTestId("board-pairing")).toBeNull();
    expect(screen.getByTestId("board-pair")).toHaveTextContent("Show key");
    act(() => engine.patch({ health: { board: "online" }, board_link: { id: "board-1" } }));
    expect(screen.getByTestId("board-status")).toHaveTextContent("online · board-1");
    act(() => engine.patch({ health: { board: "offline" } }));
    expect(screen.getByTestId("board-status")).toHaveTextContent("offline");
  });

  test("Test, Discover and Add send the right ops", async () => {
    const { engine } = await setup();
    fireEvent.click(screen.getByTestId("test-couch-left"));
    expect(engine.requests).toContainEqual({ op: "fixtures.test", fixture_id: "couch-left" });
    expect(screen.queryByTestId("test-counter-strip")).toBeNull();          // planned: nothing to blink
    await act(async () => fireEvent.click(screen.getByTestId("discover")));
    expect(engine.requests).toContainEqual({ op: "fixtures.discover" });
    fireEvent.click(screen.getByTestId("add-fixture"));
    const form = screen.getByTestId("add-form");
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Shelf" } });
    await act(async () => fireEvent.click(within(form).getByRole("button", { name: "Add" })));
    expect(engine.requests).toContainEqual({ op: "fixtures.upsert",
      fixture: { label: "Shelf", type: "virtual", status: "active", class: "fast", x: 0.5, y: 0.5 } });
    await waitFor(() => expect(screen.getByTestId("row-shelf")).toBeInTheDocument());
  });

  test("group editor: defaults to the limited group, sliders commit, flash rate tops out at 3 Hz", async () => {
    const { engine } = await setup();
    expect(screen.getByTestId("group-select")).toHaveTextContent("playbill");
    expect(within(screen.getByTestId("members")).getAllByText(/playbill-\d\d/)).toHaveLength(12);
    const flash = screen.getByTestId("max-flash");
    expect(flash).toHaveAttribute("aria-valuetext", "3 Hz");
    fireEvent.keyDown(flash, { key: "ArrowRight" });
    fireEvent.keyUp(flash, { key: "ArrowRight" });                     // already at the 3 Hz cap
    expect(engine.requests.some((r) => r.op === "groups.set_limits")).toBe(false);
    fireEvent.keyDown(flash, { key: "ArrowLeft" });
    await act(async () => fireEvent.keyUp(flash, { key: "ArrowLeft" }));
    expect(engine.requests).toContainEqual({ op: "groups.set_limits", group_id: "playbill", max_flash_hz: 2.5 });
    const level = screen.getByTestId("max-level");
    expect(level).toHaveAttribute("aria-valuetext", "60 %");
    expect(screen.getByTestId("idle-dim")).toHaveAttribute("aria-valuetext", "after 20 min");
    fireEvent.click(screen.getByTestId("group-select"));
    fireEvent.click(screen.getByRole("option", { name: /^fx/ }));
    expect(screen.getByTestId("group-select")).toHaveTextContent("fx");
  });

  test("removing a fixture asks first", async () => {
    const { engine } = await setup();
    fireEvent.click(screen.getByRole("button", { name: "Remove TV Left" }));
    expect(engine.requests.some((r) => r.op === "fixtures.remove")).toBe(false);
    await act(async () => fireEvent.click(screen.getByTestId("confirm-remove-tv-left")));
    expect(engine.requests).toContainEqual({ op: "fixtures.remove", fixture_id: "tv-left" });
    await waitFor(() => expect(screen.queryByTestId("row-tv-left")).toBeNull());
  });

  test("banks: drop a group on a cell, swap cells, keyboard assign and clear", async () => {
    const { engine } = await setup();
    const t = dt();
    fireEvent.dragStart(screen.getByTestId("group-select"), { dataTransfer: t });
    await act(async () => fireEvent.drop(screen.getByTestId("cell-C1"), { dataTransfer: t }));
    expect(engine.requests).toContainEqual({ op: "banks.set", bank: "C", channel: 1, group_id: "playbill" });
    await waitFor(() => expect(screen.getByTestId("cell-C1")).toHaveTextContent("PLAYBILL"));

    const s = dt();
    fireEvent.dragStart(screen.getByTestId("cell-A1"), { dataTransfer: s });
    await act(async () => fireEvent.drop(screen.getByTestId("cell-A2"), { dataTransfer: s }));
    expect(engine.requests).toContainEqual({ op: "banks.swap", from: { bank: "A", channel: 1 }, to: { bank: "A", channel: 2 } });
    await waitFor(() => expect(screen.getByTestId("cell-A2")).toHaveTextContent("LR ALL"));
    expect(screen.getByTestId("cell-A1")).toHaveTextContent("BED");

    fireEvent.click(screen.getByTestId("cell-B3"));                    // selects LAMP's group
    expect(screen.getByTestId("group-select")).toHaveTextContent("lamp");
    await act(async () => fireEvent.keyDown(screen.getByTestId("cell-C8"), { key: "Enter" }));
    await waitFor(() => expect(screen.getByTestId("cell-C8")).toHaveTextContent("LAMP"));
    await act(async () => fireEvent.keyDown(screen.getByTestId("cell-C8"), { key: "Delete" }));
    await waitFor(() => expect(screen.getByTestId("cell-C8")).toHaveTextContent("empty"));
  });
});
