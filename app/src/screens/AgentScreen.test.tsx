import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { renderApp } from "../test/helpers";

async function setup() {
  const r = await renderApp("mockup-console");
  r.engine.agent.delayMs = 30;
  fireEvent.click(screen.getByRole("button", { name: "Agent" }));
  expect(screen.getByTestId("agent-screen")).toBeInTheDocument();
  return r;
}

const ops = (engine: { requests: Record<string, unknown>[] }) =>
  engine.requests.filter((q) => String(q.op).startsWith("agent."));

describe("Agent tab", () => {
  test("ask → working → reply, actions, read-back look; then save and undo", async () => {
    const { engine } = await setup();
    const ask = screen.getByTestId("agent-ask");
    expect(ask).toBeDisabled();
    expect(screen.getByTestId("agent-undo")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Rainy evening, cozy but I need to read" }));
    expect(screen.getByTestId("agent-prompt")).toHaveValue("Rainy evening, cozy but I need to read");
    fireEvent.click(ask);
    await waitFor(() => expect(screen.getByTestId("agent-running")).toBeInTheDocument());
    expect(ops(engine)).toEqual([{ op: "agent.ask", prompt: "Rainy evening, cozy but I need to read" }]);
    expect(screen.getByTestId("agent-prompt")).toHaveValue("");
    expect(screen.getByTestId("agent-ask")).toBeDisabled();
    expect(screen.getByTestId("agent-why")).toHaveTextContent("Working on it…");

    await waitFor(() => expect(screen.queryByTestId("agent-running")).not.toBeInTheDocument());
    expect(screen.getByTestId("agent-reply")).toHaveTextContent("Warm low pool around the seating");
    const actions = within(screen.getByTestId("agent-actions")).getAllByRole("listitem");
    expect(actions[0]).toHaveTextContent("list_lights");
    const room = engine.state.room;
    expect(within(screen.getByTestId("look-list")).getAllByRole("listitem")).toHaveLength(room.length);
    expect(screen.getByTestId(`look-${room[0].label}`)).toHaveTextContent("#ff9a3c 20%");
    expect(screen.getByText(/WHO DRIVES is on BOARD/)).toBeInTheDocument();

    expect(screen.getByTestId("agent-name")).toHaveValue("rainy_evening_cozy_but_i_need_to");
    fireEvent.change(screen.getByTestId("agent-name"), { target: { value: "rainy" } });
    fireEvent.click(screen.getByTestId("agent-save"));
    await waitFor(() => expect(screen.getByTestId("agent-saved")).toHaveTextContent("lite scene agent.rainy"));

    fireEvent.click(screen.getByTestId("agent-undo"));
    await waitFor(() => expect(screen.getByTestId("agent-undo")).toBeDisabled());
    expect(ops(engine).map((q) => q.op)).toEqual(["agent.ask", "agent.save", "agent.undo"]);
    expect(ops(engine)[1]).toEqual({ op: "agent.save", name: "rainy" });
  });

  test("⌘↩ asks; a bad scene name can't be saved", async () => {
    const { engine } = await setup();
    const box = screen.getByTestId("agent-prompt");
    fireEvent.change(box, { target: { value: "movie night" } });
    fireEvent.keyDown(box, { key: "Enter", metaKey: true });
    await waitFor(() => expect(screen.getByTestId("agent-name")).toHaveValue("movie_night"));
    fireEvent.change(screen.getByTestId("agent-name"), { target: { value: "movie night!" } });
    expect(screen.getByTestId("agent-save")).toBeDisabled();
    expect(screen.getByText(/Names use a–z/)).toBeInTheDocument();
    expect(ops(engine)).toEqual([{ op: "agent.ask", prompt: "movie night" }]);
  });

  test("a blocked engine says why and disables Ask; errors are shown", async () => {
    const { engine } = await setup();
    act(() => engine.patch({ agent: { blocked: "the agent drives your real bulbs — switch Devices → Real to use it" } }));
    fireEvent.change(screen.getByTestId("agent-prompt"), { target: { value: "cozy" } });
    expect(screen.getByTestId("agent-ask")).toBeDisabled();
    expect(screen.getByTestId("agent-why")).toHaveTextContent("switch Devices → Real");
    act(() => engine.patch({ agent: { blocked: null, status: "error", prompt: "cozy", error: "no Anthropic API key — run `lite show setkey`" } }));
    expect(screen.getByTestId("agent-error")).toHaveTextContent("lite show setkey");
    expect(screen.getByTestId("agent-ask")).toBeEnabled();
  });

  test("the agent's state survives switching tabs", async () => {
    await setup();
    fireEvent.change(screen.getByTestId("agent-prompt"), { target: { value: "cozy" } });
    fireEvent.click(screen.getByTestId("agent-ask"));
    await waitFor(() => expect(screen.getByTestId("agent-reply")).toHaveTextContent("Warm low pool"));
    fireEvent.click(screen.getByRole("button", { name: "Console" }));
    fireEvent.click(screen.getByRole("button", { name: "Agent" }));
    expect(screen.getByTestId("agent-reply")).toHaveTextContent("Warm low pool");
    expect(screen.getByTestId("agent-undo")).toBeEnabled();
  });
});
