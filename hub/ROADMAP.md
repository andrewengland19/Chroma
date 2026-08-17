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

## Planned

- **Pass 3 — local control plane.** FastAPI + WebSocket around the engine. `state.json`
  already models `GET /state`. Reuse event names `track_change`/`colors_pushed`/`status`
  and commands `set_config`/transport/`stop`. LAN-bound.
- **Pass 4 — iPhone control PWA.** Re-point the existing React renderer (`../chroma/src`) at
  the WebSocket API; serve from the hub; add-to-home-screen.
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
