# CHROMA

Music-driven ambient lighting for LIFX bulbs, in your macOS menu bar.

See [`MANIFESTO.md`](../MANIFESTO-CHROMA.md) for the full design.

## Setup

```bash
# Python sidecar deps
pip install colorthief lifxlan Pillow

# Node deps
npm install
```

## Run (dev)

```bash
npm run dev
```

This starts Vite, compiles the Electron TS, and launches Electron once both are ready.

## Build

```bash
npm run build
npm start
```

## Layout

- `electron/main.ts` — tray icon + popover window + sidecar lifecycle
- `electron/sidecar.ts` — Python child process, stdio JSON IPC
- `electron/preload.ts` — `window.chroma` bridge
- `src/` — React + Tailwind renderer
- `python/lifx_music_app.py` — daemon (Apple Music → LIFX), JSON over stdio
