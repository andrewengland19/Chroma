export type SidecarEvent =
  | { event: "ready"; config: ChromaConfig }
  | { event: "track_change"; artist: string; title: string; album: string; artwork_path: string }
  | { event: "colors_pushed"; colors: string[]; brightness_scales: number[] }
  | { event: "white_skip"; reason: string }
  | { event: "status"; message: string }
  | { event: "error"; message: string }
  | { event: "config_updated"; key: string; value: unknown }
  | { event: "pins_updated"; colors: string[] }
  | { event: "config"; config: ChromaConfig };

export interface ChromaConfig {
  poll_interval: number;
  transition_ms: number;
  use_group: boolean;
  group_name: string;
  single_color: boolean;
  num_colors: number;
  brightness: number;
  brightness_dynamic_range: number;
  brightness_floor: number;
  white_sat_threshold: number;
  white_val_threshold: number;
  palette_oversample: number;
  neutral_kelvin: number;
}

export interface Track {
  artist: string;
  title: string;
  album: string;
  artworkPath: string;
}

export interface Pin {
  id: string;
  x: number; // 0..1
  y: number; // 0..1
  color: string; // hex
  primary: boolean;
}

export interface ChromaAPI {
  send: (cmd: unknown) => Promise<void>;
  onEvent: (cb: (e: SidecarEvent) => void) => () => void;
  openMusic: () => Promise<void>;
}

declare global {
  interface Window {
    chroma: ChromaAPI;
  }
}

export const DEFAULT_CONFIG: ChromaConfig = {
  poll_interval: 2.0,
  transition_ms: 1500,
  use_group: true,
  group_name: "Living Room",
  single_color: false,
  num_colors: 3,
  brightness: 0.75,
  brightness_dynamic_range: 1.0,
  brightness_floor: 0.25,
  white_sat_threshold: 45,
  white_val_threshold: 215,
  palette_oversample: 6,
  neutral_kelvin: 3500,
};
