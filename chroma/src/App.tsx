import React from "react";
import { ArtworkPanel } from "./components/ArtworkPanel";
import { NowPlaying } from "./components/NowPlaying";
import { ToggleButton } from "./components/ToggleButton";
import { ModeSegment } from "./components/ModeSegment";
import { ChromaSlider } from "./components/ChromaSlider";
import { LinkButton } from "./components/LinkButton";
import { useChromaState } from "./hooks/useChromaState";
import { useAccentColor } from "./hooks/useAccentColor";

export function App() {
  const s = useChromaState();
  useAccentColor(s.colors[0]);

  return (
    <div className="w-[380px] flex flex-col bg-bg text-fg">
      {/* Top region */}
      <ArtworkPanel
        track={s.track}
        pins={s.pins}
        onAddPin={s.addPin}
        onRemovePin={s.removePin}
      />

      <NowPlaying track={s.track} />

      <div className="flex items-center justify-between px-4 py-1.5">
        <div className="font-mono text-[11px] text-fg-dim truncate">
          {s.colors.slice(0, 4).map((c, i) => (
            <span key={i} className="mr-2">
              {c.toUpperCase()}
            </span>
          ))}
        </div>
        <LinkButton onClick={s.clearPins}>Reset pins</LinkButton>
      </div>

      <div className="h-px bg-border" />

      {/* Bottom region: control panel */}
      <div className="bg-surface px-4 py-3 flex flex-col gap-2.5">
        <ToggleButton active={s.running} onToggle={s.toggleRunning} />

        <ModeSegment
          value={s.config.single_color ? "single" : "palette"}
          onChange={(v) => s.setConfigKey("single_color", v === "single")}
        />

        <div className="h-px bg-border my-1" />

        <ChromaSlider
          label="Brightness"
          min={0.1}
          max={1.0}
          step={0.01}
          value={s.config.brightness}
          onChange={(v) => s.setConfigKey("brightness", v)}
          format={(v) => v.toFixed(2)}
        />
        <ChromaSlider
          label="Dynamic range"
          min={0}
          max={2.0}
          step={0.05}
          value={s.config.brightness_dynamic_range}
          onChange={(v) => s.setConfigKey("brightness_dynamic_range", v)}
          format={(v) => v.toFixed(2)}
        />
        <ChromaSlider
          label="Brightness floor"
          min={0}
          max={1.0}
          step={0.01}
          value={s.config.brightness_floor}
          onChange={(v) => s.setConfigKey("brightness_floor", v)}
          format={(v) => v.toFixed(2)}
        />
        <ChromaSlider
          label="Transition"
          min={200}
          max={5000}
          step={50}
          value={s.config.transition_ms}
          onChange={(v) => s.setConfigKey("transition_ms", v)}
          format={(v) => `${Math.round(v)}ms`}
        />

        <div className="h-px bg-border my-1" />

        <ChromaSlider
          label="White filter"
          min={10}
          max={120}
          step={1}
          value={s.config.white_sat_threshold}
          onChange={(v) =>
            s.setConfigKey("white_sat_threshold", Math.round(v))
          }
          format={(v) => Math.round(v).toString()}
          title="Saturation threshold below which colors are treated as white and skipped"
        />

        <div className="h-px bg-border my-1" />

        <div className="flex items-center justify-between pt-1">
          <LinkButton onClick={() => window.chroma.openMusic()}>
            Open Apple Music ↗
          </LinkButton>
          <LinkButton onClick={s.resetDefaults}>Reset to defaults</LinkButton>
        </div>

        <div className="font-mono text-[10px] text-fg-dim mt-1 truncate">
          {s.status}
        </div>
      </div>
    </div>
  );
}
