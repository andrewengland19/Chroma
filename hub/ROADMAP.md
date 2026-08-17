# CHROMA hub — roadmap

The hub untethers CHROMA from MacBook playback: Apple Music plays on the **Apple TV**, a
headless Mac process reads now-playing + artwork over the LAN (pyatv) and paints the LIFX
bulbs from the album art.

## Delivered

- **Pass 1 — Apple TV untether (proof).** `pair_atv.py`, `providers.py` (`AppleTVProvider`
  on the `NowPlayingProvider` seam), `color_pipeline.py`, `spike.py`. Reads now-playing +
  artwork from the Apple TV and drives the bulbs with Mac Music quit.
- **Pass 2 — headless engine.** `engine.py` + `config.py`. Runs unattended, reacts to track
  changes over pyatv's push updater (+ reconcile safety net), auto-reconnects, and writes
  the contract files (`current_colors.json` for SPECTRUM, `current_artwork.jpg` for the
  Electron UI, `state.json` for tooling).
- **Control.** `showctl.py` + `lite show {start|stop|status|restart}` — background process
  management with a PID/log under `~/.chroma/`.
- **Pass 3 — local control plane.** `server.py`: FastAPI + WebSocket embedding the engine in
  one loop. REST (`/state`, `/config`, `/transport`, `/enabled`, `/stop`), a `/ws` event
  stream (`track_change`/`colors_pushed`/`status`), a built-in status page, and `/artwork.jpg`.
  LAN-bound, no auth. `lite show start` now boots this. `EventBus` + `snapshot()` +
  `set_config`/`transport`/`set_enabled` on the engine.

- **Pass 3.5 — spatial "paint the room" + Ollama palette enhancement.** Delivered:
  - **Spatial layout:** `~/.chroma/layout.json` maps each bulb (by label) to normalized
    `(x, y)` on the album-art canvas; `spatial` mode crops the art per bulb position so the
    artwork projects across the room. Auto-seeded from labels; `GET/POST /layout`. Files:
    `layout.py`, `region_colors()` in `color_pipeline.py`, `_spatial_hsbk()` in `engine.py`.
  - **AI enhancement (`PaletteEnhancer` seam):** `enhancers.py` — `NullEnhancer` +
    `OllamaEnhancer` (aiohttp). **Model-agnostic**; default `gemma3:4b` over **LAN direct**
    (`OLLAMA_HOST=0.0.0.0` on the PC → `http://<pc-ip>:11434`). Strict timeout →
    **fallback to deterministic**; **cache per album+layout**. Key separation: the model
    returns hues/RGB (cached); the pipeline applies brightness/HSBK from live config, so the
    brightness slider stays instant and never re-runs the model. `/enhancer` reports status.
    - *Model note:* Phi-3.5-Vision fits VRAM easily but lacks clean Ollama support;
      `llava-phi3` is the supported Phi option. `qwen2.5-vl:7b` is the quality A/B. One config
      line to swap.

## Planned

- **Pass 4 — iPhone app controller (name contains "Vibrant", V logo).** Client over the Pass 3
  API — now-playing card, palette, live tuning sliders, transport. Start from the React
  renderer (`../chroma/src`) re-pointed at the WS/REST API; PWA first, native later.
  **pyatv features worth surfacing** (genuinely-useful, not flash):
  - Phone-as-remote (`RemoteControl` d-pad/select/menu/home/play_pause) — replaces the
    always-lost Siri remote (primary justification).
  - Keyboard text entry (`Keyboard.text_set`; `text_focus_state` to auto-pop the keyboard on
    an on-screen search field) — kills painful remote typing.
  - Volume (`Audio.set_volume`/`volume_up`/`down`) — quick mute/adjust.
  - **Room+screen macros — the real differentiator** (only this app controls both): "Movie
    Night" = `Power.turn_on` + `Apps.launch_app` + lights→bias scene; "Goodnight" = ATV off +
    lights off. Ties into content-aware scenes.
  - `Features.get_feature` to gray out controls a device lacks.
- **Pass 5 — first-run config + packaging.** Light discovery/selection + ATV pairing wizard;
  venv/PyInstaller bundle; launchd agent (replaces the placeholder plist); one-command
  install; survives reboot.

---

## Speculative — local AI (Ollama over LAN) for smarter art → light color

> Status: **ideas only, not scheduled.** The goal is to replace/augment the current
> ColorThief + HSV-threshold pipeline (`color_pipeline.py`) with a local vision model
> served by Ollama on the LAN (e.g. `http://<host>:11434`), for palettes that read as
> *intentional and mood-accurate* rather than merely dominant-by-pixel-count.

### Why the current pipeline leaves color on the table
- ColorThief picks the most *frequent* colors, not the most *meaningful* ones — a big white
  border or a bright logo can dominate a moody cover. The README already calls out the
  white-rejection problem for classical/jazz/minimalist art; HSV thresholds are a blunt fix.
- It ignores everything non-pixel: genre, mood, artist intent, the *subject* of the artwork.
- It has no notion of the room: bulbs are assigned round-robin, not composed spatially.

### Candidate enhancements (roughly increasing ambition)
1. **Vision-model palette extraction.** Send the artwork to a local VLM (llava,
   llama3.2-vision, qwen2-vl, moondream) and ask for the N most *emotionally dominant,
   non-background* colors as hex. Sidesteps the white/logo problem semantically instead of
   with thresholds.
2. **Mood/genre → lighting grammar.** Feed track metadata (artist/title/album, maybe genre)
   to a text model that emits *pipeline params*, not colors — warm vs cool bias, saturation,
   `brightness_dynamic_range`, floor. The model tunes `PipelineParams`/`Config`; ColorThief
   still supplies the raw hues. Cheap, fast, and a natural first step.
3. **Color-harmony refinement.** Hand the model ColorThief's raw palette and ask it to
   re-rank / nudge toward a harmonious set (complementary/analogous), de-clash, and ensure
   the multi-bulb assignment looks deliberate.
4. **LIFX-gamut perceptual correction.** A calibration step: model (or a learned LUT) maps
   sRGB art colors → the HSBK that *actually looks* like that color on LIFX bulbs in a warm
   room. Bulbs distort hues, especially low-kelvin; pre-correct so the room matches the art.
5. **Spatial scene composition.** Given the room's fixed bulb layout (TV left/right, couch
   left/right, etc. — already known in SPECTRUM's `config.py`), have the model design a
   gradient/focal composition across the bulbs rather than round-robin.
6. **Preference learning.** With the Pass 4 phone UI, let the user 👍/👎 a painting; store
   feedback per album/genre and feed it as few-shot context to steer future palettes.

### Architecture fit (keep it a clean, optional seam)
- Add a `PaletteEnhancer` seam parallel to `NowPlayingProvider`. The engine's `_apply()`
  calls `enhance(rgb_or_hsbk, artwork_bytes, now_playing)`; implementations:
  - `NullEnhancer` (default — current behavior, no AI), and
  - `OllamaEnhancer` (POSTs artwork + prompt to the LAN Ollama endpoint).
- New config: `ai_enhance: bool`, `ollama_url: str`, `ollama_model: str`, `ai_timeout_ms`.
- **Latency & resilience are non-negotiable.** Inference runs *off the hot path* (executor /
  async), with a strict timeout and a **fallback to the current pipeline** on any error or
  slowness — mirroring SPECTRUM's existing `CHROMA_FALLBACK_COLOR` pattern. A track must
  never wait on the model.
- **Cache per album.** Key results by album id (or artwork hash) so the model runs once per
  album, not per poll — keeps it snappy and mostly offline.
- Surface the AI toggle + model picker in the Pass 3 control plane / Pass 4 phone UI.

### Open questions
- Which local model gives the best color/latency tradeoff on the target hardware?
- Structured output: enforce a JSON schema (hex list + rationale) vs. free-text parsing.
- Do we enhance the RGB palette, the final HSBK, or the `Config` params — or a mix?
- Is per-album caching enough, or do we want per-track (live remixes, singles vs albums)?

---

## Speculative — productize into a MOSH-suite app

> Status: **ideas only, not scheduled.** Vision: graduate the hub from a headless
> `lite show` daemon into a polished consumer app in the MOSH suite — an iOS (and tvOS)
> front end over the Pass 3 control plane, so the whole thing is set-and-forget with a
> real UI, no terminal.

### Name — decided direction
**The name should contain "Vibrant"** (e.g. *Vibrant*, *Vibrance*), with a stylized **V**
logo. **Second choice: Afterglow.** Earlier shortlist kept below for reference.

The product is "your room reacts to whatever's on your screen." Naming leans into
ambient/bias-lighting, not "music visualizer."
- **Afterglow** — warm, evocative; the glow around the screen. (Second choice.)
- **Backdrop** — the room as a living backdrop to what you're watching.
- **Bias / BiasLight** — *bias lighting* is the actual A/V term for ambient light behind a
  TV; the insider-cool pick.
- **Aura** — the room's aura follows the content.
- **Limelight / Halo / Wash / Gel** — stage-lighting vocabulary (a color "wash", light
  "gels", the "limelight").
- **Lumen** — clean, brandable unit-of-light name.

### iOS / tvOS app features
- **Now-Playing card** — mirror the current artwork + live extracted palette (reuse the
  existing `../chroma/src` renderer: artwork panel, color pins, accent-reactive sliders).
- **Manual paint / pin override** — tap the art to force a color (the pin system already
  exists in the Electron UI); great for parties.
- **Scene library** — save/name/apply scenes (SPECTRUM already has a scenes model in
  `~/.spectrum_scenes.json` worth converging on).
- **Live tuning** — brightness / dynamic range / white-filter / transition sliders over the
  Pass 3 API (these are already `Config` fields).
- **"Follow the Apple TV" master toggle** + per-room device groups; multi-room later.
- **Home-screen widget / Live Activity** showing the current palette + track/show.
- **Siri Shortcuts / automations** — "Movie Night" sets a profile; Focus-mode & time-of-day
  awareness (dimmer after 11pm).
- **tvOS companion** — control from the couch with the Siri remote; on-screen scene picker.
- **Pairing wizard** — wrap `pair_atv.py` in a friendly first-run flow (feeds Pass 5).

---

## Speculative — content-aware scenes (the room reacts to *what's on screen*)

> Status: **ideas only, not scheduled.** The neat discovery: pyatv reports far more than
> music. It exposes what app is open and what kind of media is playing, including TV show
> details — so the room can switch lighting *modes* based on what you're actually watching.

### What pyatv already gives us (grounded, verified in 0.18)
- `Metadata.app` → `App.identifier` + `App.name` (Netflix, YouTube, the TV app, Music, …).
- `Playing.media_type` → `Music | TV | Video | Unknown`.
- For shows: `Playing.series_name`, `season_number`, `episode_number`, `genre`, `title`,
  `content_identifier`. **So "Jeopardy → scene" is a match on `series_name`, not ML.**

### Feature ideas
1. **Media-type routing.** `Music` → today's album-art palette mode. `TV`/`Video` →
   **bias-lighting mode**: a calm, dim, desaturated wash (or a poster-derived tone) instead
   of a dancing palette — easier on the eyes for a 2-hour movie.
2. **Per-show / per-app scene bindings.** A rules table mapping a match → a scene or profile:
   - `series_name == "Jeopardy!"` → the blue "Jeopardy" scene.
   - `series_name == "The Big Bang Theory"` → warm sitcom wash.
   - `app.name == "Netflix"` → default bias-light; `app.name == "Music"` → art palette.
   - `genre == "Horror"` → deep red, low, slow.
   Precedence: explicit title/series binding > app default > media-type default > art palette.
3. **Sports mode.** Team detection from title/metadata → team colors (stretch; metadata is
   inconsistent across apps).
4. **Auto-scene via the AI seam.** For anything without an explicit binding, let the local
   model read the show title / poster art and *invent* a fitting scene — "auto-scene for
   everything," reusing the Ollama `PaletteEnhancer` above.
5. **Rules UI.** Author bindings from the phone app; store alongside scenes. Live-reload like
   SPECTRUM's scenes file so edits land without a restart.

### The honest boundary: metadata scenes vs. true ambilight
- **Metadata-driven scenes (easy, do this):** everything above keys off pyatv metadata
  (app / media_type / series_name / poster art). Fully in reach with the current stack.
- **Per-frame "ambilight" (hard, different project):** matching the *live* on-screen colors
  frame-by-frame needs the actual video buffer, which pyatv does **not** provide. That
  requires HDMI capture hardware (Philips Hue Sync-box style) or screen-mirror capture — out
  of scope for the LAN/metadata approach. Poster/artwork tone is the closest metadata-only
  approximation.
