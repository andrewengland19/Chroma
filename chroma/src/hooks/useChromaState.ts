import { useCallback, useEffect, useRef, useState } from "react";
import { ChromaConfig, DEFAULT_CONFIG, Pin, SidecarEvent, Track } from "../types";

let pinIdCounter = 0;
const newPinId = () => `pin-${++pinIdCounter}`;

export function useChromaState() {
  const [config, setConfig] = useState<ChromaConfig>(DEFAULT_CONFIG);
  const [track, setTrack] = useState<Track | null>(null);
  const [colors, setColors] = useState<string[]>([]);
  const [status, setStatus] = useState<string>("Idle");
  const [running, setRunning] = useState<boolean>(false);
  const [pins, setPins] = useState<Pin[]>([]);
  const pinsRef = useRef<Pin[]>([]);
  pinsRef.current = pins;

  useEffect(() => {
    const off = window.chroma.onEvent((e: SidecarEvent) => {
      switch (e.event) {
        case "ready":
          setConfig({ ...DEFAULT_CONFIG, ...e.config });
          break;
        case "config":
          setConfig({ ...DEFAULT_CONFIG, ...e.config });
          break;
        case "config_updated":
          setConfig((c) => ({ ...c, [e.key]: e.value as never }));
          break;
        case "track_change":
          setTrack({
            artist: e.artist,
            title: e.title,
            album: e.album,
            artworkPath: e.artwork_path,
          });
          break;
        case "colors_pushed":
          setColors(e.colors);
          break;
        case "status":
          setStatus(e.message);
          if (/^Active/.test(e.message)) setRunning(true);
          if (/^Stopped/.test(e.message) || /No LIFX/.test(e.message)) setRunning(false);
          break;
        case "error":
          setStatus(`Error: ${e.message}`);
          break;
        case "white_skip":
          setStatus(`Skipped: ${e.reason}`);
          break;
        case "pins_updated":
          // server confirms; nothing to do
          break;
      }
    });
    return off;
  }, []);

  const send = useCallback((cmd: unknown) => window.chroma.send(cmd), []);

  const setConfigKey = useCallback(
    <K extends keyof ChromaConfig>(key: K, value: ChromaConfig[K]) => {
      setConfig((c) => ({ ...c, [key]: value }));
      send({ cmd: "set_config", key, value });
    },
    [send],
  );

  const start = useCallback(() => {
    setRunning(true);
    send({ cmd: "start" });
  }, [send]);

  const stop = useCallback(() => {
    setRunning(false);
    send({ cmd: "stop" });
  }, [send]);

  const toggleRunning = useCallback(() => {
    if (running) stop();
    else start();
  }, [running, start, stop]);

  const syncPinsToSidecar = useCallback(
    (next: Pin[]) => {
      if (next.length === 0) {
        send({ cmd: "clear_pins" });
      } else {
        send({ cmd: "set_colors", colors: next.map((p) => p.color) });
      }
    },
    [send],
  );

  const addPin = useCallback(
    (x: number, y: number, color: string, primary: boolean) => {
      setPins((prev) => {
        let next: Pin[];
        if (primary) {
          // primary replaces existing primary
          next = [
            { id: newPinId(), x, y, color, primary: true },
            ...prev.filter((p) => !p.primary),
          ];
        } else {
          const maxAccent = Math.max(0, config.num_colors - 1);
          const accents = prev.filter((p) => !p.primary);
          const accentNext =
            accents.length >= maxAccent ? accents.slice(1) : accents;
          next = [
            ...prev.filter((p) => p.primary),
            ...accentNext,
            { id: newPinId(), x, y, color, primary: false },
          ];
        }
        syncPinsToSidecar(next);
        return next;
      });
    },
    [config.num_colors, syncPinsToSidecar],
  );

  const removePin = useCallback(
    (id: string) => {
      setPins((prev) => {
        const next = prev.filter((p) => p.id !== id);
        syncPinsToSidecar(next);
        return next;
      });
    },
    [syncPinsToSidecar],
  );

  const clearPins = useCallback(() => {
    setPins([]);
    send({ cmd: "clear_pins" });
  }, [send]);

  const resetDefaults = useCallback(() => {
    const fields: (keyof ChromaConfig)[] = [
      "brightness",
      "brightness_dynamic_range",
      "brightness_floor",
      "transition_ms",
      "white_sat_threshold",
      "single_color",
    ];
    for (const k of fields) {
      setConfigKey(k, DEFAULT_CONFIG[k] as never);
    }
  }, [setConfigKey]);

  return {
    config,
    track,
    colors,
    status,
    running,
    pins,
    setConfigKey,
    toggleRunning,
    addPin,
    removePin,
    clearPins,
    resetDefaults,
  };
}
