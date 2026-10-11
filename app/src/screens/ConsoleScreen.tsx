// Console (gui-1). M2: the live header (WHO DRIVES, ART MODE, Now Playing, transport,
// status chips) and the Room view. COLOR, CUES, faders and the activity strip are
// laid out per the mockup and filled in M4/M5.
import type { ArtMode, Who } from "../contracts";
import { NowPlayingCard } from "../components/NowPlayingCard";
import { RoomView } from "../components/RoomView";
import { StatusChips, TransportButtons } from "../components/Transport";
import { Panel, Segmented } from "../components/ui";
import { useSelect, useStore } from "../store";

function Pending({ label, note }: { label: string; note: string }) {
  return (
    <>
      <div className="label absolute" style={{ left: 11, top: 8 }}>{label}</div>
      <div className="absolute inset-0 flex items-center justify-center text-label" style={{ fontSize: 9 }}>{note}</div>
    </>
  );
}

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
      <Panel style={{ left: 19, top: 314, width: 324, height: 145 }}>
        <Pending label="Color" note="hue · sat · palette — M4" />
      </Panel>
      <Panel style={{ left: 19, top: 469, width: 324, height: 140 }}>
        <Pending label="Cues" note="cue pads — M4" />
      </Panel>
      <Panel style={{ left: 358, top: 108, width: 801, height: 501 }}>
        <Pending label="Bank" note="faders, flash, assign, bank, fader function, master — M4" />
      </Panel>
      <Panel style={{ left: 19, top: 618, width: 1140, height: 57 }}>
        <Pending label="" note="recording activity — M5" />
      </Panel>
    </div>
  );
}
