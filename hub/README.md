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
| `layout.py` | **Pass 3.5** — bulb `(x,y)` positions + region cropping |
| `enhancers.py` | **Pass 3.5** — `PaletteEnhancer` seam (`NullEnhancer`, `OllamaEnhancer`) |
| `distribute.py` | **Pass 4** — round-robin + OKLab IDW colour distribution |
| `web/` | **Pass 4** — the web GUI (`index.html`, `app.js`, `style.css`) |
| `showctl.py` | background start/stop (`lite show …`), now launches `server.py` |
| `spike.py` | Pass 1 proof (one-shot; superseded by `engine.py`) |

## 6. Web GUI (Pass 4)

`lite show start` serves a full control GUI at `http://<mac-ip>:8765/` (open it on desktop
or phone). One **bidirectional** WebSocket carries state + events out and commands in:
- **Room canvas** — drag each light to its real `(x,y)`; nodes show live per-bulb colour.
- **Modes** — Deterministic (art per region), Paint, AI (Ollama reasoning shown live).
- **Paint** — click the album art to sample a colour (press-hold for a magnifier lens);
  pick 1+ colours; toggle **round-robin** (even split by position) vs **spatial** (OKLab
  inverse-distance blend across the room).
- **Tuning** — brightness / dynamic-range / floor / transition sliders (live).

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

## 5. Spatial "paint the room" + AI enhancement (Pass 3.5)

Two composable upgrades to how the room is painted:

**Spatial mode** projects the album art across the room. Each bulb has an `(x, y)` position
on the art canvas (`~/.chroma/layout.json`, auto-seeded from bulb labels, editable live);
in `spatial` mode each bulb is colored from the art region at its position.

```bash
curl -X POST localhost:8765/config -d '{"mode":"spatial"}'   # project art across the room
curl -X POST localhost:8765/config -d '{"mode":"classic"}'   # back to single/palette
curl -s localhost:8765/layout | jq                            # bulb positions (0..1)
curl -X POST localhost:8765/layout -d '{"lights":{"TV Left":{"x":0.2,"y":0.3}}}'
```

**AI enhancement** (optional) refines the per-region colors with a local Ollama vision model
over the LAN. The model returns hues (cached per album); brightness/HSBK still come from live
config, and any timeout/error falls back to the deterministic palette — so a track never
waits on the model and it all works with the PC off.

```bash
# On the PC:  set OLLAMA_HOST=0.0.0.0 and `ollama pull gemma3:4b`
curl -X POST localhost:8765/config -d '{"ai_enhance":true,"ollama_url":"http://<pc-ip>:11434"}'
curl -s localhost:8765/enhancer | jq     # model, reachable, last reasoning
```

`ollama_model` is config — swap to `llava-phi3`, `qwen2.5-vl:7b`, etc. without code changes.

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
- **Pass 3.5 — ✅ DONE (verified live, uncommitted pending review).** Spatial "paint the
  room" (2-D `(x,y)` layout, `spatial` mode paints each bulb from its art region) +
  `PaletteEnhancer` seam (Ollama, model-agnostic, timeout→fallback→per-album cache).
  Verified: spatial paints 5 distinct per-bulb colors mapped to position; `/layout` seeds +
  edits live; dead-Ollama fallback is fast and safe. AI idle until `ollama_url` is set.
  Survived a real sleep/wake cycle (auto-reconnect held).
- **Pass 4 — ✅ DONE (verified live, uncommitted pending review).** Hub-served web GUI
  (`hub/web/`, vanilla JS + Canvas) over a now-**bidirectional** `/ws`. Room canvas with
  draggable lights (live per-bulb colors), album-art paint picker (click-to-sample +
  press-hold magnifier), 3 modes (deterministic/paint/ai), AI reasoning panel. New
  `hub/distribute.py` (round-robin + OKLab IDW). Engine `mode` refactored to
  {deterministic,paint,ai} with migration. Verified: GUI renders + mode switch + room canvas;
  paint round-robin splits bulbs by position, spatial blends L→R; per-bulb `colors_pushed`.
  Scope pivoted from the standalone iPhone/Expo app (browser is reachable from the phone).
- **Pass 4 —** iPhone control PWA (re-point `../chroma/src` at the WS API).
- **Pass 5 —** First-run config + packaging + launchd.
