// Takes (gui-2): library on the left; transport, timeline and details of the selected take.
import { useEffect, useState } from "react";
import { Details } from "../components/takes/Details";
import { Library } from "../components/takes/Library";
import { TakesBar, type RecMode } from "../components/takes/TakesBar";
import { Timeline } from "../components/takes/Timeline";
import type { TakeEvent, TakeHeader } from "../contracts";
import { useSelect, useSongPosition, useStore } from "../store";

export function TakesScreen() {
  const store = useStore();
  const takes = useSelect((s) => s.takes);
  const rev = useSelect((s) => s.state?.takes_rev);
  const playId = useSelect((s) => s.state?.play.take_id ?? null);
  const now = useSelect((s) => s.state?.now);
  const songPos = useSongPosition();
  const [selected, setSelected] = useState<string | null>(null);
  const [take, setTake] = useState<{ header: TakeHeader; events: TakeEvent[] } | null>(null);
  const [mode, setMode] = useState<RecMode>("merge");
  const [loop, setLoop] = useState(false);

  // default selection: the take playing, else the newest
  useEffect(() => {
    if (!takes) return;
    if (selected && takes.some((t) => t.id === selected)) return;
    setSelected(playId && takes.some((t) => t.id === playId) ? playId : takes[0]?.id ?? null);
  }, [takes, selected, playId]);

  useEffect(() => {
    let live = true;
    if (!selected) {
      setTake(null);
      return;
    }
    void store.ipc.request({ op: "takes.get", take_id: selected }).then((r) => {
      if (live && r.ok) setTake({ header: r.header as TakeHeader, events: r.events as TakeEvent[] });
    });
    return () => {
      live = false;
    };
  }, [selected, rev, store]);

  const header = take?.header.id === selected ? take.header : null;
  const onSong = !!header && !!now && (now.track_id === header.track_id || (now.title === header.title && now.artist === header.artist));
  const pos = onSong ? songPos ?? 0 : 0;
  return (
    <div data-testid="takes-screen">
      <Library selected={selected} onSelect={setSelected} />
      <TakesBar header={header} mode={mode} setMode={setMode} loop={loop} setLoop={setLoop} pos={pos} />
      <Timeline header={header} events={header ? take!.events : []} pos={pos} canSeek={onSong} />
      <Details header={header} onSelect={setSelected} />
    </div>
  );
}
