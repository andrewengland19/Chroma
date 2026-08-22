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
| `enhancers.py` | **Pass 3.5/4.6** — `PaletteEnhancer` seam (`NullEnhancer`, `OllamaEnhancer`, `ClaudeEnhancer`) |
| `keystore.py` | **Pass 4.6** — Anthropic API key from env / macOS Keychain |
| `distribute.py` | **Pass 4** — round-robin + OKLab IDW colour distribution |
| `scenes.py` | **Pass 4.5** — persistent per-album scenes (`~/.chroma/scenes.json`) |
| `web/` | **Pass 4** — the web GUI (`index.html`, `app.js`, `style.css`) |
| `showctl.py` | background start/stop (`lite show …`), now launches `server.py` |
| `tv_cmd.py` | **`tv` CLI** — rapid-fire Apple TV remote from any terminal |
| `tv` | shell wrapper — `exec .venv/bin/python tv_cmd.py "$@"` |
| `tv_hotkey.py` | global hotkey daemon — `Ctrl+Shift+Enter` → `tv ok` (select) |
| `com.chroma.tv-hotkey.plist` | LaunchAgent to run `tv_hotkey.py` at login |
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

## 7. Per-album scenes (Pass 4.5)

The hub remembers the *exact* lighting for an album and reproduces it automatically whenever
that album plays again — persisted to `~/.chroma/scenes.json`, keyed by `artist|||album`.
Only meaningful work is saved (deterministic output is cheap and isn't):
- **AI** — the first time an album is AI-enhanced, its result (colours + reasoning) is saved;
  later plays reuse it with **no model call**.
- **Paint** — making a custom paint scene while an album plays saves it for that album.

A saved scene **overrides the global mode** for its album, so you can sit in AI mode generally
but pin a hand-painted look to specific albums. The GUI shows a "★ saved scene" bar with a
**Forget** button (`scene_clear`) to revert an album to the global mode.

## 8. AI backends: Ollama (auto) + Claude fallback (Pass 4.6)

The AI enhancer has two backends. **Ollama (local) is preferred and automatic:** a health
monitor pings it, and when it's reachable the hub auto-enables AI mode. When Ollama is
unreachable (Meshnet/PC down), the hub **flags it on the web homescreen, falls back to
deterministic (the room keeps painting), and never auto-switches to Claude** — Claude is a
manual, opt-in override (it costs money and is remote).

- **Store the key once** (macOS login Keychain; never in git/config):

```bash
lite show setkey            # prompts, input hidden
```

- The GUI shows an **offline banner** with a **Use Claude** button (enabled only when a key is
  stored). Or drive it directly: `curl -X POST localhost:8765/backend -d '{"backend":"claude"}'`.
- Model: `claude-opus-5` (config `claude_model`). Because Pass 4.5 caches each album's result,
  Claude runs at most once per new album — pennies per album.
- Key resolution: `ANTHROPIC_API_KEY` env first, else the Keychain item `chroma-hub/anthropic`.

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

## 9. `tv` — rapid-fire Apple TV CLI

Control the Apple TV from any terminal without touching the remote:

```bash
# Install once
ln -sf /Users/andy/Code/Chroma/hub/tv /usr/local/bin/tv

tv now                  # ▶ Artist — Title [Album]
tv pause / play / stop  # transport
tv toggle               # play-pause toggle
tv next / prev          # track navigation
tv vol up 3             # volume up 3 notches
tv vol down             # volume down 1 notch
tv skip 30              # skip forward 30 s
tv back 10              # skip back 10 s
tv up/down/left/right   # d-pad navigation
tv select / ok          # select button
tv menu / home / top    # menu, home, top-menu
tv wake / sleep         # power on / off
tv apps                 # list launchable apps
tv launch music         # launch app by partial name
```

## 10. Global hotkey: `Ctrl+Shift+Enter` → `tv ok`

A background daemon (`tv_hotkey.py`) registers **⌃⇧↵** system-wide and fires
`tv ok` (the select button) — handy for skipping theme songs from the couch.

### Install (one time)

**1. Grant Accessibility access** — macOS requires it for global hotkeys:

> System Settings → Privacy & Security → Accessibility  
> Click **+** and add: `/Users/andy/Code/Chroma/hub/.venv/bin/python3`

**2. Load the LaunchAgent** (starts now and at every login):

```bash
ln -sf /Users/andy/Code/Chroma/hub/com.chroma.tv-hotkey.plist \
       ~/Library/LaunchAgents/com.chroma.tv-hotkey.plist
launchctl load ~/Library/LaunchAgents/com.chroma.tv-hotkey.plist
```

Check it's running:

```bash
launchctl list | grep chroma
tail -f /tmp/tv-hotkey.log
```

Stop / restart:

```bash
launchctl unload ~/Library/LaunchAgents/com.chroma.tv-hotkey.plist
launchctl load   ~/Library/LaunchAgents/com.chroma.tv-hotkey.plist
```

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
- **Pass 4.6 — ✅ DONE (verified live).** Claude/Anthropic fallback: `ClaudeEnhancer`
  (`claude-opus-5`, vision + json_schema), `keystore.py`, `lite show setkey`. Ollama health
  monitor auto-enables AI when reachable and flags offline + falls to deterministic when not —
  Claude stays manual. Verified: offline banner + disabled Use-Claude (no key) in the browser;
  graceful no-key degrade. Scenes now carry a `source` field for future training data.
- **Pass 4.5 — ✅ DONE.** Persistent per-album scenes (`scenes.py`): AI results + custom paint
  scenes saved per album and auto-recalled (overriding the global mode); "Forget" reverts.
  Verified live: paint scene saved to disk, recalled over a deterministic global mode, cleared.
- **Pass 4 — ✅ DONE (verified live).** Hub-served web GUI
  (`hub/web/`, vanilla JS + Canvas) over a now-**bidirectional** `/ws`. Room canvas with
  draggable lights (live per-bulb colors), album-art paint picker (click-to-sample +
  press-hold magnifier), 3 modes (deterministic/paint/ai), AI reasoning panel. New
  `hub/distribute.py` (round-robin + OKLab IDW). Engine `mode` refactored to
  {deterministic,paint,ai} with migration. Verified: GUI renders + mode switch + room canvas;
  paint round-robin splits bulbs by position, spatial blends L→R; per-bulb `colors_pushed`.
  Scope pivoted from the standalone iPhone/Expo app (browser is reachable from the phone).
- **Pass 4 —** iPhone control PWA (re-point `../chroma/src` at the WS API).
- **Pass 5 —** First-run config + packaging + launchd.
