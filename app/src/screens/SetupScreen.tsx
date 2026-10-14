// Setup (gui-3): fixtures table, group editor with safety limits, bank grid.
import { useState } from "react";
import { BanksPanel } from "../components/setup/BanksPanel";
import { FixturesPanel } from "../components/setup/FixturesPanel";
import { GroupPanel } from "../components/setup/GroupPanel";

export function SetupScreen() {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <div data-testid="setup-screen">
      <FixturesPanel />
      <GroupPanel selected={selected} onSelect={setSelected} />
      <BanksPanel selected={selected} onSelect={setSelected} />
    </div>
  );
}
