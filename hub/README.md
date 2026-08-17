# CHROMA hub

Untethers CHROMA from MacBook playback: reads now-playing + album artwork from the
**Apple TV** over the LAN (via [pyatv](https://pyatv.dev)) and drives the LIFX bulbs —
so Apple Music can play directly on the Apple TV with no Mac in the loop.

This folder is **Pass 1**: an isolated spike proving the Apple TV path works, plus the
`NowPlayingProvider` seam that later passes build the real headless engine on. It does
not touch the existing Electron app, root daemons, or Spectrum.

## Setup

```bash
cd hub
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## 1. Pair the Apple TV (one time)

Run this in your terminal (it prompts for the PIN shown on the TV):

```bash
./.venv/bin/python pair_atv.py
```

Pick your Apple TV from the scan, then type the 4-digit PIN(s) it displays. Credentials
are saved to `~/.chroma/atv_credentials.json`. Re-run any time to re-pair.

## 2. Prove the untether

```bash
# Quit Apple Music on the Mac first, then play a track ON the Apple TV.
./.venv/bin/python spike.py
```

Expected: prints `artist — title`, saves artwork to `~/.chroma/atv_artwork.jpg`, prints
the HSBK palette, and (unless `CHROMA_NO_LIGHTS=1`) changes your LIFX bulbs. Change songs
on the Apple TV and the push listener reacts within ~1s.

Options:
- `CHROMA_LIFX_GROUP="Living Room" ./.venv/bin/python spike.py` — limit to a LIFX group.
- `CHROMA_NO_LIGHTS=1 ./.venv/bin/python spike.py` — steps 1–4 only, no bulbs.

## 3. Run the headless engine (Pass 2)

The daily driver. Runs forever, reacts to track changes on the Apple TV, paints the
bulbs, and writes the contract files. Reads `~/.chroma/config.json` (your tuned CHROMA
settings) live on every track.

```bash
# Quit Apple Music on the Mac; play on the Apple TV.
./.venv/bin/python engine.py
```

Writes:
- `~/.chroma/current_colors.json` — SPECTRUM BEAT-mode palette contract
- `~/.chroma/current_artwork.jpg` — artwork the Electron UI serves
- `~/.chroma/state.json` — engine status (feeds the Pass 3 control plane)

Auto-reconnects when the Apple TV sleeps or the network blips. Ctrl+C (or SIGTERM under
launchd, Pass 5) shuts it down cleanly.

## Files

| File | Purpose |
|---|---|
| `pair_atv.py` | scan + pair AirPlay/Companion, save credentials |
| `providers.py` | `NowPlaying`, `NowPlayingProvider` Protocol, `AppleTVProvider` |
| `color_pipeline.py` | pure color functions (copied from `../lifx_music_app.py`), `PipelineParams` |
| `config.py` | `Config` — persisted tunables (`~/.chroma/config.json`) |
| `engine.py` | **headless daily-driver engine** — connect → react → paint → write contracts |
| `server.py` | **Pass 3 control plane** — FastAPI + WebSocket embedding the engine |
| `showctl.py` | background start/stop (`lite show …`), now launches `server.py` |
| `spike.py` | Pass 1 proof (one-shot; superseded by `engine.py`) |

## 4. Control plane (Pass 3)

`lite show start` now boots `server.py`, which embeds the engine and serves a LAN API on
`:8765` (no auth — trusted home network). Point a browser at `http://<mac-ip>:8765/` for a
live status page, or drive it directly:

```bash
curl -s localhost:8765/state | jq                     # full snapshot incl. config
curl -X POST localhost:8765/config -d '{"brightness":0.6}'   # repaints live
curl -X POST localhost:8765/transport -d '{"action":"next"}' # Apple TV transport
curl -X POST localhost:8765/enabled -d '{"on":false}'        # pause painting, stay connected
websocat ws://localhost:8765/ws                       # live track_change/colors_pushed/status
```

Run the engine **without** the API (headless debug): `./.venv/bin/python engine.py`.

## Progress Ledger

Updated at each pass boundary so the next pass resumes with no re-derivation.

- **Pass 1 — ✅ DONE.** Pairing + spike verified live: bulbs painted from Apple TV
  playback with Mac Music quit.
- **Pass 2 — ✅ DONE.** `engine.py` headless daily-driver validated live (Big Screen → 5
  Living Room bulbs; contract files written; reads tuned `~/.chroma/config.json`).
  Async-native with auto-reconnect + reconcile safety-net.
- **Pass 3 — ✅ DONE.** `server.py` (FastAPI + WebSocket) embeds the engine in one loop.
  Verified live: `/state` snapshot, `/config` live-repaints the bulbs, `/transport`
  play/pause toggles the Apple TV (reflected live in `/state`), `/enabled` pauses painting,
  `/ws` streams events, status page + `/artwork.jpg` reachable from the phone at
  `http://192.168.0.150:8765/`. `lite show start` now boots the server.
- **Pass 3.5 — NEXT.** Ollama palette enhancement (`PaletteEnhancer` seam). Then Pass 4:
  the "Vibrant" iPhone app controller over this API.
- **Pass 4 —** iPhone control PWA (re-point `../chroma/src` at the WS API).
- **Pass 5 —** First-run config + packaging + launchd.
