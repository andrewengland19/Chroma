// The mock engine's rig: gui-3's data (contracts/examples/fixtures/ok-mockup.json) plus
// in-memory versions of the fixtures/groups/banks ops, mirroring hub/fixtures.py's rules
// that the UI depends on (flash rate <= 3 Hz, ids, banks).
import mockupRig from "../../../contracts/examples/fixtures/ok-mockup.json";
import type { FixturesFile, Fixture, Group } from "../contracts";
import type { Reply } from "../ipc/types";

export class MockRig {
  rig: FixturesFile = structuredClone(mockupRig) as FixturesFile;
  constructor(private bump: (rev: number) => void) {}

  private changed() {
    this.rig.rev += 1;
    this.bump(this.rig.rev);
  }

  online(): Record<string, boolean> {
    return Object.fromEntries(this.rig.fixtures.map((f) => [f.id, f.status === "active"]));
  }

  view() {
    return { ...structuredClone(this.rig), online: this.online() };
  }

  handle(req: Record<string, unknown>): Reply {
    const r = this.rig;
    switch (req.op) {
      case "fixtures.list":
        return { ok: true, ...this.view() };
      case "fixtures.discover":
        return { ok: true, result: { found: 9, added: [], updated: [] }, ...this.view() };
      case "fixtures.upsert": {
        const fx = { ...(req.fixture as Fixture) };
        if (!fx.id) fx.id = fx.label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        const i = r.fixtures.findIndex((f) => f.id === fx.id);
        if (i >= 0) r.fixtures[i] = fx;
        else r.fixtures.push(fx);
        this.changed();
        return { ok: true, fixture: fx, rev: r.rev };
      }
      case "fixtures.remove":
        r.fixtures = r.fixtures.filter((f) => f.id !== req.fixture_id);
        r.groups.forEach((g) => (g.members = g.members.filter((m) => m !== req.fixture_id)));
        this.changed();
        return { ok: true, rev: r.rev };
      case "fixtures.test":
        return { ok: true };
      case "groups.list":
        return { ok: true, groups: r.groups, rev: r.rev };
      case "groups.upsert": {
        const g = req.group as Group;
        if (!(g.max_flash_hz > 0 && g.max_flash_hz <= 3)) return { ok: false, error: "max flash rate must be above 0 and at most 3 Hz" };
        const i = r.groups.findIndex((x) => x.id === g.id);
        if (i >= 0) r.groups[i] = g;
        else r.groups.push(g);
        this.changed();
        return { ok: true, group: g, rev: r.rev };
      }
      case "banks.get":
        return { ok: true, banks: r.banks, rev: r.rev };
      case "banks.set": {
        const bank = req.bank as "A" | "B" | "C";
        r.banks[bank][(req.channel as number) - 1] = (req.group_id as string | null) ?? null;
        this.changed();
        return { ok: true, banks: r.banks, rev: r.rev };
      }
    }
    return { ok: false, error: `unknown op '${String(req.op)}'` };
  }
}
