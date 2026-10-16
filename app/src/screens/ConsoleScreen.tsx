// Console (gui-1): the live header (WHO DRIVES, ART MODE, Now Playing, transport,
// status chips), Room, COLOR, CUES, the fader bank (M4) and the recording activity
// strip (M5).
import type { ArtMode, Who } from "../contracts";
import { NowPlayingCard } from "../components/NowPlayingCard";
import { ActivityStrip } from "../components/console/ActivityStrip";
import { BankPanel } from "../components/console/BankPanel";
import { ColorPanel, CuesPanel } from "../components/console/ColorCues";
import { RoomView } from "../components/RoomView";
import { StatusChips, TransportButtons } from "../components/Transport";
import { Segmented } from "../components/ui";
import { useSelect, useStore } from "../store";

export function ConsoleScreen() {
  const store = useStore();
  const who = useSelect((s) => s.state?.who);
  const artMode = useSelect((s) => s.state?.art_mode);
  return (
    <div data-testid="console-screen">
      <div className="absolute" style={{ left: 19, top: 45 }}>
        <Segmented<Who> label="Who drives" testId="who" width={123} value={who}
          options={[{ value: "auto", label: "AUTO" }, { value: "board", label: "BOARD" }]}
          onChange={(who) => void store.intent({ intent: "who", who })} />
      </div>
      <div className="absolute" style={{ left: 167, top: 45 }}>
        <Segmented<ArtMode> label="Art mode" testId="art-mode" width={158} value={artMode}
          options={[{ value: "det", label: "DET" }, { value: "paint", label: "PAINT" }, { value: "ai", label: "AI" }]}
          onChange={(mode) => void store.op("set_art_mode", { mode })} />
      </div>
      <NowPlayingCard />
      <TransportButtons />
      <StatusChips />
      <RoomView />
      <ColorPanel />
      <CuesPanel />
      <BankPanel />
      <ActivityStrip />
    </div>
  );
}
