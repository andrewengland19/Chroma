// Mock of hub/agentsvc.py: agent.ask runs "in the background" for `delayMs`, then
// publishes a canned reply and a look made from the room's lights.
import type { AgentState, State } from "../contracts";
import type { Reply } from "../ipc/types";

const NAME_RE = /^[a-z0-9_-]{1,32}$/;
const LOOK = ["#ff9a3c", "#ffb35c", "#1f6f7a", "#2a4d8f", "#ffd8a0", "#c2410c", "#0e7490", "#6d28d9"];

export class MockAgent {
  delayMs = 1500;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(private state: () => State, private patch: (p: Record<string, unknown>) => void) {}

  private set(p: Partial<AgentState>) {
    this.patch({ agent: p });
  }

  /** Saved lite scenes for scenes.list (newest first); tests can replace it. */
  scenes: { name: string; colors: string[] }[] = [
    "sunset", "movie_night", "focus", "reading", "party", "morning", "candle", "dim_hall", "aurora", "storm", "cozy", "late_snack",
  ].map((n, i) => ({ name: `agent.${n}`, colors: [LOOK[i % LOOK.length], LOOK[(i + 3) % LOOK.length], LOOK[(i + 5) % LOOK.length]] }));

  handleScenes(req: Record<string, unknown>): Reply {
    const a = this.state().agent;
    if (a.blocked) return { ok: false, error: a.blocked };
    if (req.op === "scenes.list") return { ok: true, scenes: this.scenes.map((s) => ({ ...s })) };
    if (req.op === "scenes.recall") {
      const name = String(req.name ?? "");
      if (!this.scenes.some((s) => s.name === name)) return { ok: false, error: `no scene '${name}'` };
      this.patch({ who: "board" });
      return { ok: true };
    }
    return { ok: false, error: `unknown op '${String(req.op)}'` };
  }

  handle(req: Record<string, unknown>): Reply {
    const a = this.state().agent;
    if (a.blocked) return { ok: false, error: a.blocked };
    if (a.status === "running") return { ok: false, error: "the agent is still working on the last request" };
    switch (req.op) {
      case "agent.ask": {
        const prompt = typeof req.prompt === "string" ? req.prompt.trim() : "";
        if (!prompt) return { ok: false, error: "say what the room should look like" };
        if (prompt.length > 500) return { ok: false, error: "keep it under 500 characters" };
        this.patch({
          who: "board",
          agent: { status: "running", prompt, reply: "", suggested_name: null, actions: [], look: [], error: null,
            can_undo: false, saved_as: null },
        });
        this.timer = setTimeout(() => this.finish(prompt), this.delayMs);
        return { ok: true };
      }
      case "agent.undo":
        if (!a.can_undo) return { ok: false, error: "nothing to undo" };
        this.set({ can_undo: false, look: a.look.map((l) => ({ ...l, hex: "#ffd8a0", brightness: 0.6, power: "on" as const })) });
        return { ok: true };
      case "agent.save": {
        const name = String(req.name ?? "");
        if (!NAME_RE.test(name)) return { ok: false, error: "name: 1-32 of a-z, 0-9, _ or -" };
        this.set({ saved_as: `agent.${name}` });
        return { ok: true, saved: `agent.${name}` };
      }
      default:
        return { ok: false, error: `unknown op '${String(req.op)}'` };
    }
  }

  private finish(prompt: string) {
    this.timer = null;
    const room = this.state().room;
    const look = room.map((r, i) => ({ label: r.label, power: "on" as const, hex: LOOK[i % LOOK.length],
      brightness: Math.round((0.2 + 0.1 * (i % 5)) * 100) / 100 }));
    const name = prompt.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 32) || null;
    this.set({
      status: "done",
      reply: `Warm low pool around the seating, the TV side dimmer and cooler — "${prompt}".`,
      suggested_name: name && NAME_RE.test(name) ? name : null,
      actions: [
        { tool: "list_lights", summary: `read ${room.length} bulbs` },
        { tool: "set_lights", summary: look.slice(0, 3).map((l) => `${l.label}: ${l.hex} @${l.brightness.toFixed(2)}`).join(", ") },
      ],
      look,
      can_undo: true,
    });
  }

  dispose() {
    if (this.timer) clearTimeout(this.timer);
  }
}
