# CHROMA

> A macOS menu bar app that paints your room in the colors of whatever you're listening to.

CHROMA bridges Apple Music and LIFX smart bulbs over your local LAN. Every track change triggers a full color extraction pipeline: album artwork is fetched, dominant colors are pulled from it, white tones are filtered out, and the result is pushed to your lights — with smooth transitions, per-bulb brightness variation, and a live accent that flows through the UI itself.

---

## What it looks like

The entire app lives in a 380px popover anchored to the menu bar icon. No dock entry, no separate window. Click the icon and everything is right there — now-playing info, album artwork with interactive color pins, and a control panel whose accent color updates with every track.

---

## Architecture

CHROMA is built as a two-process Electron app:

```
┌────────────────────────────────┐        ┌──────────────────────────────┐
│         Electron (Node)        │  stdin │       Python sidecar         │
│  main.ts — tray, popover,      │ ──────▶│  lifx_music_app.py           │
│  protocol handler              │        │  • Polls Apple Music via      │
│                                │ stdout │    AppleScript               │
│  sidecar.ts — spawns Python,   │ ◀────  │  • Fetches album artwork     │
│  parses JSON events, forwards  │  JSON  │  • Runs ColorThief palette   │
│  to renderer via IPC           │  lines │    extraction                │
│                                │        │  • Filters white/grey tones  │
│  React renderer                │        │  • Scales brightness by hue  │
│  • ArtworkPanel + color pins   │        │  • Pushes HSBK to LIFX LAN   │
│  • Custom sliders              │        └──────────────────────────────┘
│  • Live CSS accent variable    │
└────────────────────────────────┘
```

### IPC protocol

The Python sidecar and Electron main process communicate over `stdin`/`stdout` using newline-delimited JSON — a deliberate choice that keeps the processes fully decoupled and language-agnostic.

**Sidecar → Electron (stdout):**
```jsonc
{ "event": "track_change", "artist": "MAVI", "title": "Daylight Savings", "album": "Let the Sun Talk", "artwork_path": "/Users/andy/.chroma/current_artwork.jpg" }
{ "event": "colors_pushed", "colors": ["#3e4d5c", "#ad5432"], "brightness_scales": [0.71, 0.63] }
{ "event": "white_skip", "reason": "majority white artwork" }
{ "event": "status", "message": "Discovering lights…" }
```

**Electron → Sidecar (stdin):**
```jsonc
{ "cmd": "set_config", "key": "brightness", "value": 0.82 }
{ "cmd": "set_colors", "colors": ["#3e4d5c", "#ad5432"] }
{ "cmd": "clear_pins" }
{ "cmd": "stop" }
```

### Color pipeline

1. **Artwork fetch** — tries three sources in order: artwork URL from AppleScript, raw JPEG from Apple Music's temp file, iTunes Search API fallback.
2. **Palette extraction** — ColorThief with oversampling (`num_colors + palette_oversample`) to give the white filter enough candidates to discard.
3. **White rejection** — converts each RGB candidate to HSV; rejects if saturation falls below `white_sat_threshold` or if it's bright and near-grey (`val > white_val_threshold && sat < threshold × 1.5`).
4. **Brightness scaling** — computes per-bulb brightness by measuring each color's value (V in HSV) relative to the palette mean, then applying `brightness + (deviation × dynamic_range)`, clamped to `[brightness_floor, 1.0]`. This produces a live lighting feel that mirrors the drama of the artwork.
5. **HSBK conversion** — maps RGB to LIFX's native Hue/Saturation/Brightness/Kelvin format and sends to each bulb over the LAN with a configurable fade duration.

### Renderer

The UI is a React + Tailwind app with no component library. All interactive controls are custom-built:

- **`ChromaSlider`** — fully custom slider built with pointer events on a `div` track. No native `<input type="range">` appearance. The filled portion uses the current dominant color via `var(--accent)`.
- **`ArtworkPanel`** — displays album art at full width with a `position: absolute` pin overlay. Click places a primary pin, `Ctrl+Click` places an accent pin. Pins are stored as fractional coordinates (0–1) so they survive any resize. When pins are active, the Python sidecar's automatic extraction is bypassed entirely.
- **`useAccentColor`** — extracts the dominant color from the current artwork and injects it as `--accent` on `:root`. Every interactive element in the UI picks it up automatically — sliders, the active toggle, pin borders.

Artwork is served through a custom Electron protocol (`chroma-art://`) registered before `app.whenReady()`, which bypasses the cross-origin restrictions that would block `file://` URLs from the renderer.

---

## Tech stack

| Layer | Technology |
|---|---|
| Desktop shell | Electron 29, TypeScript |
| Renderer | React 18, Tailwind CSS 3, Vite 5 |
| Python sidecar | Python 3, lifxlan, ColorThief, Pillow |
| Music integration | AppleScript via `osascript` |
| IPC | stdin/stdout newline-delimited JSON |
| Config persistence | `~/.chroma/config.json` (written by main process only) |
| Build | `tsc` for Electron, Vite for renderer, `concurrently` for dev |

---

## Prerequisites

- macOS (AppleScript dependency)
- Apple Music with at least one song playing
- LIFX bulbs on the same LAN
- Node.js ≥ 18
- Python 3.10+

---

## Setup

**1. Install Python dependencies:**
```bash
pip install colorthief lifxlan Pillow
```

**2. Install Node dependencies:**
```bash
cd chroma
npm install
```

**3. Run in development mode:**
```bash
npm run dev
```

This starts the Vite dev server, compiles the Electron TypeScript in watch mode, and launches Electron once both are ready.

**4. (Optional) Configure your LIFX group:**

By default CHROMA targets a group named `"Living Room"`. To change it, edit `~/.chroma/config.json` after first launch, or use the sliders in the popover.

---

## Controls

| Control | What it does |
|---|---|
| Click artwork | Place a primary color pin — bypasses auto extraction and sends this color directly to the lights |
| Ctrl+Click artwork | Place an accent pin (up to `num_colors - 1` accent pins) |
| Right-click a pin | Remove that pin |
| Reset pins | Clear all pins and revert to automatic ColorThief extraction |
| Brightness | Master ceiling — the brightest bulb reaches this level |
| Dynamic range | `0` = flat (all bulbs same brightness), `1` = natural, `2` = dramatic contrast |
| Brightness floor | Minimum brightness any bulb will reach |
| Transition | Fade duration per color change, in milliseconds |
| White filter | Saturation threshold below which colors are treated as white and discarded |
| Single / Palette | Toggle between one shared color or a per-bulb palette |

---

## Project structure

```
chroma/
├── electron/
│   ├── main.ts          # Tray icon, popover window, protocol handler
│   ├── preload.ts       # Context bridge — exposes IPC to renderer
│   └── sidecar.ts       # Spawns Python, parses stdout, routes IPC
├── src/
│   ├── App.tsx
│   ├── components/
│   │   ├── ArtworkPanel.tsx     # Artwork + pin overlay
│   │   ├── ChromaSlider.tsx     # Custom accent-reactive slider
│   │   ├── ToggleButton.tsx     # Start/stop pill
│   │   ├── ModeSegment.tsx      # Single | Palette switcher
│   │   ├── NowPlaying.tsx       # Track/artist/album strip
│   │   └── LinkButton.tsx       # Text link
│   ├── hooks/
│   │   ├── useChromaState.ts    # Central state: track, colors, config, pins
│   │   └── useAccentColor.ts    # Derives --accent CSS var from artwork
│   └── types.ts                 # Shared TypeScript types + IPC protocol
└── python/
    └── lifx_music_app.py        # Sidecar: AppleScript, ColorThief, lifxlan
```

---

## Why CHROMA exists

I wanted a version of the Apple TV music player's ambient lighting mode — the one that samples your album art and bathes the room in it — but for a desktop setup. Nothing off-the-shelf did this with Apple Music on macOS, so I built it.

The technical challenge I found most interesting was the white-rejection problem: naive palette extraction from most album artwork returns a lot of near-white or near-grey, especially for classical, jazz, and minimalist cover art. Getting the filter tuned so it aggressively rejects grey without also swallowing muted earth tones required the two-threshold HSV approach and surfacing it as a user-adjustable parameter.

---

## License

MIT
