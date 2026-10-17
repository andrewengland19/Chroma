// State of the Console's Cues pane in Scenes mode: the saved lite scenes (scenes.list),
// which of them have pads, and the one last recalled. Shared by CuesPanel and ScenesModal.
import { useCallback, useEffect, useState } from "react";
import { useSelect, useStore } from "../../store";

export const SCENE_SLOTS = 8;                       // pads in the Cues pane
const SHOWN_KEY = "chroma.scenes.shown";
export type SceneSummary = { name: string; colors: string[] };
export type CuesMode = "cues" | "scenes";

function readShown(): string[] | null {
  try {
    const v = JSON.parse(localStorage.getItem(SHOWN_KEY) ?? "null");
    return Array.isArray(v) && v.every((x) => typeof x === "string") ? v : null;
  } catch {
    return null;
  }
}

export function useScenesPane() {
  const store = useStore();
  const cueActive = useSelect((s) => s.state?.cue_active);
  const [mode, setMode] = useState<CuesMode>("cues");
  const [scenes, setScenes] = useState<SceneSummary[] | null>(null);
  const [shown, setShownState] = useState<string[] | null>(readShown);   // null = never chosen: the first 8
  const [active, setActive] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const r = await store.op("scenes.list");
    if (r.ok && Array.isArray(r.scenes)) setScenes(r.scenes as SceneSummary[]);
  }, [store]);
  useEffect(() => {
    if (mode === "scenes") void refresh();           // a scene saved on the Agent tab shows up
  }, [mode, refresh]);
  useEffect(() => setActive(null), [cueActive]);     // a cue recall replaced the scene

  const all = scenes ?? [];
  const visible = (shown ? all.filter((s) => shown.includes(s.name)) : all).slice(0, SCENE_SLOTS);
  const setShown = (names: string[]) => {
    setShownState(names);
    try {
      localStorage.setItem(SHOWN_KEY, JSON.stringify(names));
    } catch { /* storage blocked: the choice lasts until restart */ }
  };
  const recall = async (name: string) => {
    const r = await store.op("scenes.recall", { name });
    if (r.ok) setActive(name);
  };
  return { mode, setMode, all, visible, active, recall, setShown, loaded: scenes !== null };
}
export type ScenesPane = ReturnType<typeof useScenesPane>;
