# CHROMA — Project Manifesto
### A macOS menu bar app for music-driven ambient lighting
**Author:** Andrew England / andrewengland19  
**Created:** 2026-05-16  

---

## What CHROMA Is

CHROMA is a macOS menu bar application that bridges Apple Music and LIFX smart bulbs, painting your living room in the colors of whatever you're listening to. It watches Apple Music in real time, extracts dominant colors from album artwork, and pushes them to a specified group of LIFX bulbs over the local LAN — with smooth transitions, white rejection, and per-bulb brightness variation that mirrors the dynamic feel of the Apple TV music player.

CHROMA lives in the menu bar. Click the icon and a popover drops down. That is the entire surface area of the app. No separate window, no dock icon, no system tray clutter.

---

## Name & Identity

**CHROMA** — from Greek χρῶμα, meaning color, complexion, skin. It also references chroma key, the cinematographic technique of color-driven scene transformation. The name should feel precise and slightly technical, not playful. One word, all caps in logotype, lowercase in code (`chroma`).

---

## Tech Stack

- **Framework:** Electron + TypeScript (same pattern as MOSH)
- **UI:** React + Tailwind CSS (utility-first, no component libraries)
- **IPC:** Electron's `ipcMain` / `ipcRenderer` for renderer ↔ main process communication
- **Python sidecar:** The existing `lifx_music_app.py` daemon logic runs as a child process spawned by Electron's main process. Electron manages its lifecycle (start, stop, kill on quit). The Python process communicates over stdout/stderr — it emits JSON lines for track changes, color decisions, and errors. Electron parses these and forwards to the renderer via IPC.
- **Config persistence:** JSON file at `~/.chroma/config.json`, written by the main process, never by the renderer directly.
- **Album artwork:** The Python sidecar writes the current artwork JPEG to `~/.chroma/current_artwork.jpg` on every track change. The renderer loads this via a `file://` URL and displays it.

---

## Layout — The Popover

The popover is a fixed-width panel (380px wide) that anchors to the menu bar icon and drops down. It has no title bar, no window chrome, no resize handle. It dismisses when you click outside it (standard macOS popover behavior via Electron's `BrowserWindow` with `alwaysOnTop` and focus-loss detection).

The popover is divided into two vertical regions:

### Top Region — Now Playing + Artwork + Color Pins

The top half of the popover is dominated by the **album artwork** — displayed at full width (380px), aspect-ratio-locked to square (so 380×380px). This is the primary visual anchor of the app.

Overlaid on top of the artwork are **color pin markers** — small circular indicators (12px diameter) showing the currently selected sample points. Each pin has:
- A colored fill matching the color it represents
- A thin white ring border
- A subtle drop shadow so it reads against any artwork color

**Interaction model for the artwork:**
- **Click anywhere on the artwork** → places a primary color pin at that pixel. Samples the color at that exact pixel coordinate and feeds it directly to the LIFX algorithm as the dominant color override.
- **Ctrl+Click** → places a secondary/accent color pin. Up to `NUM_COLORS - 1` accent pins can exist simultaneously.
- **Right-click any existing pin** → removes that pin.
- **"Reset pins" button** (text link, below the artwork) → removes all pins and reverts to automatic ColorThief extraction.

When pins are active, the Python sidecar's automatic color extraction is bypassed entirely — the pinned colors are sent directly. Pin positions are stored as fractional coordinates (0.0–1.0) relative to the artwork dimensions so they survive window resizes.

Below the artwork, a slim strip (48px tall) shows:
- **Track title** in medium weight, truncated with ellipsis
- **Artist — Album** in a dimmer secondary style below it
- This strip uses the dominant color from the artwork as a background tint (low opacity, ~15%), the same way the Apple TV player does it

### Bottom Region — Controls

The bottom region is a clean control panel. Sections are separated by 1px hairline dividers. No section headers — the grouping is visually self-evident from proximity and spacing.

**Row 1 — Start/Stop toggle**  
A single pill-shaped toggle button. Left state: `▶ Start tracking`. Right state: `⏹ Stop`. The toggle fills with the current dominant color when active, giving a live pulse of what color the lights are currently set to. Inactive state is neutral dark.

**Row 2 — Color mode**  
A segmented control: `Single` | `Palette`. Switches between `SINGLE_COLOR = True` and the multi-color palette mode. 

**Rows 3–6 — Sliders**  
Four sliders, each a single row with label on the left and live numeric readout on the right:
- **Brightness** (0.10 – 1.00)
- **Dynamic range** (0.0 – 2.0)
- **Brightness floor** (0.00 – 1.00)  
- **Transition** (200ms – 5000ms)

Sliders use a thin track with a circular thumb. The filled portion of the track uses the current dominant color as its fill, so the whole UI subtly reflects what's playing.

**Row 7 — White rejection threshold**  
A single slider labeled "White filter" that maps to `WHITE_SAT_THRESHOLD` (10–120). A tooltip on hover explains what it does.

**Row 8 — Open Apple Music**  
A small text link: `Open Apple Music ↗`. Launches Music.app via `open -a Music`.

**Row 9 — Reset to defaults**  
A small text link: `Reset to defaults`. Resets all sliders to their default values (does not remove color pins — that's the "Reset pins" button above).

---

## Visual Design Language

CHROMA's aesthetic is **instrumental and cinematic** — the UI should feel like a broadcast graphics package, not a consumer app. It takes visual cues from the album artwork it displays, using the dominant color as a live accent throughout the interface.

### Color System

All colors are CSS custom properties defined in a `:root` block:

```css
--bg: #0e0e0e           /* near-black background */
--surface: #161616      /* slightly lifted surface for the control panel */
--border: #2a2a2a       /* hairline dividers */
--fg: #f0f0f0           /* primary text */
--fg-dim: #666666       /* secondary text, labels */
--accent: /* computed live from dominant artwork color */
--accent-muted: /* accent at 20% opacity, for fills */
```

The accent color is injected as a CSS variable by the renderer when a new track loads. Every interactive element — slider fills, the active toggle, the segmented control selection, pin borders — picks this up automatically. When no track is playing, the accent defaults to `#888`.

### Typography

- **Display (track title):** `"SF Pro Display"`, 14px, weight 600. Fall back to `-apple-system`.
- **Body (artist/album, labels):** `"SF Pro Text"`, 12px, weight 400.
- **Mono (slider values, hex codes):** `"SF Mono"`, 11px. Hex color values shown next to each active pin.
- **Links:** 11px, `var(--fg-dim)`, underline on hover only.

All type is set with `letter-spacing: -0.01em` on headings, `0` on body.

### Slider Design

Sliders are fully custom — no native `<input type="range">` appearance. Built with a `div`-based track + thumb using pointer events. The track is 3px tall, rounded. Filled portion uses `var(--accent)`. Thumb is 14px circular, white fill, 1px border matching accent, subtle box-shadow.

### Artwork Display

The artwork image fills the full 380px width. Corners are square (not rounded) — the image should feel like a piece of material being examined, not a card. A very subtle inner vignette (radial gradient overlay, black at 0% center → 25% opacity at edges) gives it depth without obscuring colors.

The color pins use `position: absolute` inside a wrapper div that matches the image dimensions. Pins animate in with a small scale-up on placement (`transform: scale(0) → scale(1)`, 120ms ease-out).

### Motion

- Track transitions: when a new track loads, the artwork fades out and in (opacity 0→1, 200ms). The accent color transition uses a CSS variable animation so sliders and fills update smoothly over ~300ms.
- Slider thumbs: `transition: transform 80ms` on `:active` (slight scale-up on grab).
- Pin placement: `transform: scale(0) → scale(1)` on mount, 120ms ease-out.
- Popover open: no animation (follows macOS popover convention — instant).

---

## IPC Protocol — Python ↔ Electron

The Python sidecar emits newline-delimited JSON to stdout. The Electron main process reads this stream and forwards relevant events to the renderer via `ipcMain.emit`.

### Python → Electron events (stdout JSON lines):

```jsonc
{ "event": "track_change", "artist": "MAVI", "title": "Daylight Savings", "album": "Let the Sun Talk", "artwork_path": "/Users/andy/.chroma/current_artwork.jpg" }
{ "event": "colors_pushed", "colors": ["#3e4d5c", "#ad5432"], "brightness_scales": [0.71, 0.63] }
{ "event": "white_skip", "reason": "majority white artwork" }
{ "event": "status", "message": "Discovering lights…" }
{ "event": "error", "message": "LAN discovery failed: …" }
```

### Electron → Python (stdin JSON lines):

```jsonc
{ "cmd": "set_config", "key": "brightness", "value": 0.82 }
{ "cmd": "set_config", "key": "single_color", "value": true }
{ "cmd": "set_colors", "colors": ["#3e4d5c", "#ad5432"] }  // pin override
{ "cmd": "clear_pins" }                                      // revert to auto
{ "cmd": "stop" }
{ "cmd": "start" }
```

The Python sidecar must be modified to:
1. Accept stdin commands and apply them to the live `cfg` object.
2. Write artwork to `~/.chroma/current_artwork.jpg` on every track change.
3. Emit the JSON event lines above to stdout.

---

## File Structure

```
chroma/
├── MANIFESTO.md
├── package.json
├── tsconfig.json
├── tailwind.config.js
├── electron/
│   ├── main.ts          # Electron main process: window, tray, sidecar spawn
│   ├── preload.ts       # Context bridge: exposes IPC to renderer
│   └── sidecar.ts       # Python process management + stdout parsing
├── src/
│   ├── App.tsx          # Root component: layout, IPC event subscriptions
│   ├── components/
│   │   ├── ArtworkPanel.tsx     # Image display + pin overlay
│   │   ├── ColorPin.tsx         # Individual draggable pin
│   │   ├── NowPlaying.tsx       # Track title/artist/album strip
│   │   ├── ToggleButton.tsx     # Start/stop pill toggle
│   │   ├── ModeSegment.tsx      # Single | Palette segmented control
│   │   ├── ChromaSlider.tsx     # Custom slider (reused for all 5 sliders)
│   │   └── LinkButton.tsx       # Small text link (Open Music, Reset)
│   ├── hooks/
│   │   ├── useChromaState.ts    # Central state: track, colors, config, pins
│   │   └── useAccentColor.ts    # Extracts dominant color from artwork for CSS var
│   ├── styles/
│   │   └── globals.css          # CSS custom properties, resets
│   └── index.tsx
├── python/
│   └── lifx_music_app.py        # Modified daemon (stdin commands + JSON stdout)
└── assets/
    └── icon.png                 # Menu bar icon (template image, monochrome)
```

---

## Python Sidecar Modifications Required

The existing `lifx_music_app.py` needs the following additions before Claude Code begins:

1. **Artwork export:** After fetching artwork bytes on track change, write them to `os.path.expanduser("~/.chroma/current_artwork.jpg")` before extracting colors.

2. **JSON stdout events:** Replace all `log.info(f"♪ …")` and `log.info(f"→ …")` track/color log lines with `print(json.dumps({...}), flush=True)` calls matching the protocol above. Keep `log.*` for debug/error lines that are not part of the IPC protocol.

3. **stdin command loop:** Spawn a thread that reads `sys.stdin` line by line. Each line is parsed as JSON. Handle `set_config`, `set_colors`, `clear_pins`, `start`, and `stop` commands by mutating the live `cfg` object and a `pin_colors` list that the daemon thread checks before running ColorThief.

4. **Pin color override:** In the daemon loop, before calling `extract_colors()`, check if `pin_colors` is non-empty. If it is, skip ColorThief entirely and use `pin_colors` directly as the RGB palette.

---

## What Claude Code Should Build First

1. Scaffold the Electron + TypeScript + React + Tailwind project structure above.
2. Implement `electron/main.ts`: tray icon, popover window (frameless, `alwaysOnTop`, focus-loss dismiss), Python sidecar spawn.
3. Implement `electron/sidecar.ts`: spawn Python, pipe stdout to IPC, pipe IPC commands to stdin.
4. Implement `ArtworkPanel.tsx` with the pin interaction model (click, ctrl+click, right-click, fractional coordinates).
5. Implement `ChromaSlider.tsx` with accent-color-reactive track fill.
6. Wire everything together in `App.tsx` via `useChromaState`.
7. Apply the visual design language above throughout.

The Python sidecar modifications are a prerequisite — Claude Code should apply them to `python/lifx_music_app.py` before building the Electron layer, since the IPC contract needs to be stable before the renderer can be tested.

---

## Non-Goals

- No settings in a separate window. Everything lives in the popover.
- No dock icon. `app.dock.hide()` in `main.ts`.
- No auto-update mechanism (manual for now).
- No onboarding flow. Config defaults are reasonable; user edits via sliders.
- No support for Spotify, Tidal, or other players. Apple Music only.
- No cloud sync of config. Local JSON only.
