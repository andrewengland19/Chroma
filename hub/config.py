# =============================================================================
# hub/config.py
# The hub's single source of truth, persisted to ~/.chroma/config.json.
# Mirrors the fields of the old lifx_music_app.py Config, minus the AppleScript
# poll_interval (the ATV push updater replaces polling). Pass 3's control plane
# mutates this live; the engine re-reads it on every track.
# =============================================================================

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, fields

from color_pipeline import PipelineParams

CONFIG_PATH = os.path.expanduser("~/.chroma/config.json")


@dataclass
class Config:
    # Light targeting
    use_group: bool = False          # False = every LIFX bulb on the LAN
    group_name: str = "Living Room"  # used only when use_group is True

    # Color mode
    single_color: bool = False
    num_colors: int = 3

    # Brightness
    brightness: float = 0.75
    brightness_dynamic_range: float = 1.0
    brightness_floor: float = 0.25

    # White rejection
    white_sat_threshold: int = 45
    white_val_threshold: int = 215
    palette_oversample: int = 6

    # Timing / white balance
    transition_ms: int = 1500
    neutral_kelvin: int = 3500

    # Behaviour
    reconcile_seconds: float = 15.0  # safety re-poll in case a push update is missed
    enabled: bool = True             # False = stay connected but don't paint the bulbs

    # Control plane (Pass 3) — LAN-bound, no auth (trusted home network)
    api_host: str = "0.0.0.0"
    api_port: int = 8765

    def pipeline_params(self) -> PipelineParams:
        return PipelineParams(
            white_sat_threshold=self.white_sat_threshold,
            white_val_threshold=self.white_val_threshold,
            palette_oversample=self.palette_oversample,
            brightness=self.brightness,
            brightness_dynamic_range=self.brightness_dynamic_range,
            brightness_floor=self.brightness_floor,
            neutral_kelvin=self.neutral_kelvin,
        )

    def color_count(self, num_lights: int) -> int:
        if self.single_color:
            return 1
        return min(self.num_colors, num_lights) if num_lights else self.num_colors

    def save(self) -> None:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, CONFIG_PATH)  # atomic

    @classmethod
    def load(cls) -> "Config":
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH) as f:
                    data = json.load(f)
                known = {f.name for f in fields(cls)}
                return cls(**{k: v for k, v in data.items() if k in known})
            except Exception:
                pass
        return cls()
