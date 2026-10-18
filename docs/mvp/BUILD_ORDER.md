# CHROMA Console — MVP build order

**Audience:** the engineer/agent who will build this. **Author's note:** this is the spec. As of 2026-10-18 M0–M5 are built (status and evidence live in
`PROGRESS.md`; M6–M7 are pending). Where something could not be verified it is marked **⚠** — treat those as "measure or confirm
before relying on it" and report what you found.

Read in this order: §0 rules → §1–2 what and why → §3 repo → §4 architecture → §5 contracts → §6 milestones.
Mockups: [Console](gui-1-console.png) · [Takes](gui-2-takes.png) · [Setup](gui-3-setup.png).
Hardware context (physical board, Playbill bars) lives in `assets/lighting-board-*.svg` and
`docs/lighting-board-build-steps.md`; this document covers **software only**.

---

## 0. Rules for the builder

1. **Build in the order of §6.** Each milestone ends with acceptance checks (AC). Do not start the next
   milestone until the current AC passes and is demonstrated (test output or a short written demo).
2. **Do not break what works today.** `lite show start|stop|status|restart|refresh` and the existing hub
   (`hub/engine.py`, `hub/server.py`) must keep working until M7 explicitly cuts over.
3. **The mockups are approved. Match them.** If you need UI that is not in a mockup (e.g. the board pairing
   card in M6), build it minimally and flag it for review. Do not redesign screens.
4. **Pass 5 features already exist on `main`** and are the user's: the natural-language light agent
   (`hub/lights_agent.py`, `hub/lights_prompt.py`, `/agent*`), the Lights tab (`hub/web/lights.html`,
   `/lights*`, `/scenes*`), the TV Remote tab (`hub/web/remote.html`, `/tv*`) and the Ctrl+Shift+L hotkey.
   They are **out of MVP scope but must keep working**; do not remove or rewrite them (see M7 and §10).
5. **Safety rules in §8 are requirements, not suggestions.** They protect people (photosensitivity) and
   property (fixtures near wood and paper).
6. **No secrets in the repo.** The Anthropic key stays in the macOS Keychain (`hub/keystore.py`). The board
   pairing key lives in `~/.chroma/`, never in git.
7. **Prefer small, reviewable commits on a branch per milestone** (`app/m3-fixtures` etc.). Ask before
   deleting directories (`archive/`, `hub/web/`).
8. **Do not write ESP32 firmware here.** The board protocol (§5.5) is defined so firmware can be written
   separately; you only build the engine side and a simulator.

## 1. The product

A native Mac app that is a **lighting console** for the user's living-room lights, driven by faders and
buttons, with the album-art painter as an automatic mode. The user plays a song on the Apple TV, plays the
console like an instrument, **records** what they did against the song, and can **replay** it later. The
recordings are also the dataset for training an AI to perform in the user's style (later phases).

Three screens (see mockups): **Console** (perform), **Takes** (recordings + timeline), **Setup** (fixtures,
groups, banks, safety limits).

**The key invariant:** the on-screen console and the physical board are *interchangeable controllers*. Both
send the same intents (§5.2) to the same engine, which produces the same effect. There must be no feature
that exists only on screen or only on the board.

## 2. Decisions already made

| Topic | Decision |
|---|---|
| App shell | **Tauri 2** (Rust shell + web UI). Not Electron. |
| UI | React + TypeScript + Tailwind + Vite (the existing `archive/legacy/chroma/src` renderer is a reference; port freely). |
| Engine | **Python sidecar `chromad`**, evolved from `hub/engine.py`. Apple TV support (pyatv) is Python-only, so no Rust rewrite. |
| UI ↔ engine | **Unix domain socket**, newline-delimited JSON (§5.1). No HTTP/TCP for the UI. The webview never talks to the socket; the Rust shell proxies. |
| Board ↔ engine | One **UDP** port, LAN, authenticated datagrams, **state-snapshot** protocol (§5.5). |
| Outputs (MVP) | **LIFX** (existing lifxlan path) and **Virtual** lights (in-app). WLED is phase 2. |
| Existing web GUI | The Music page is superseded by the app. **The Lights and Remote pages and their routes are not part of the MVP and must stay working** until the user decides otherwise. Ask before deleting anything. |
| Platform | macOS first. Linux/Pi is a later target. |

## 3. Repo map

Exists today (reuse, do not move):

```
hub/engine.py        Engine, LifxController, EventBus, snapshot(), refresh(), _render(), _apply()
hub/providers.py     NowPlaying, AppleTVProvider (NowPlayingProvider seam)
hub/color_pipeline.py, hub/distribute.py, hub/enhancers.py   album art -> colors, AI palette
hub/scenes.py (SceneStore), hub/layout.py (Layout, per-bulb x,y)  per-album scenes, room positions
hub/config.py        Config (~/.chroma/config.json)
hub/server.py        FastAPI + WS + hub/web/*  (kept; includes /agent, /lights, /scenes, /tv for Pass 5)
hub/lights_agent.py, hub/lights_prompt.py, hub/web/lights.html, hub/web/remote.html   Pass 5 (user-owned, keep working)
hub/showctl.py       background process control (lite show ...)
hub/keystore.py      Keychain access for the Anthropic key
archive/legacy/chroma/  older Electron menu-bar app (archived 2026-10-18); React components worth porting; delete only with approval
```

New (create):

```
contracts/           JSON Schema + example messages for every contract in §5 (source of truth)
hub/chromad.py       new entrypoint: Engine + IPC server + board link
hub/console.py       console model, intents, arbitration, cues (pure logic, no I/O)
hub/fixtures.py      fixtures / groups / banks model + persistence
hub/outputs/         base.py (Output interface), lifx.py, virtual.py
hub/clock.py         SongClock (interpolated song position)
hub/takes.py         take file format, recorder, replayer
hub/ipc.py           Unix-socket NDJSON server
hub/board_link.py    UDP board protocol
hub/testing/         FakeProvider (scripted tracks w/ position), helpers
hub/tests/           pytest
tools/chromactl.py   debug CLI over the IPC socket (state, watch, intent)
tools/board_sim.py   board simulator (CLI + scriptable + replays a take as a "board")
app/                 Tauri 2 app: src-tauri/ (Rust), src/ (React+TS)
```

State on disk (all under `~/.chroma/`): `fixtures.json`, `console.json` (palette, cues, last bank, fn),
`takes/*.jsonl`, `board.key`, plus existing files. Never write outside `~/.chroma/` at runtime.

## 4. Architecture

```
Chroma.app (Tauri 2)
  UI (React) <--Tauri invoke/events--> Rust shell: window, tray, supervisor, IPC proxy
                                              |  Unix socket ~/.chroma/chromad.sock (0600), NDJSON
                                        chromad (Python, asyncio)
   Apple TV (pyatv) -> NowPlaying + SongClock          Board (ESP32) --UDP--> board_link
   art -> colors (existing pipeline)                  UI / board / replay all emit INTENTS
   fixtures/groups/banks  <-  console.py  ->  outputs: LIFX | Virtual
   recorder/replayer (takes.py)
```

- **Intents are the only way to change console state.** UI, board, replay and CLI all go through
  `console.apply(intent)`. This is what guarantees interchangeability and makes recording trivial (record the
  intents).
- **chromad outlives the window.** Closing the window must not stop the lights. In M2 the app supervises
  chromad (spawn if the socket is absent, restart with backoff); in M7 it can also run as a LaunchAgent and
  the app merely attaches.
- **Realtime path:** controller -> chromad -> output. The UI is never in the realtime path; it receives
  state patches (<= 30 Hz, coalesced).

## 5. Contracts

Put each in `contracts/` as JSON Schema with example files, and add a pytest that validates the examples.
Generate TS types from the schemas for the UI. If a contract must change, change the schema first.

### 5.1 IPC (UI <-> chromad)

Framing: UTF-8 JSON objects, one per line. Requests carry `id`; responses echo it; events carry `ev`.

```jsonc
// request                           // response
{"id":7,"op":"intent","intent":{...}}      {"id":7,"ok":true}
{"id":8,"op":"get_state"}                  {"id":8,"ok":true,"state":{...}}
{"id":9,"op":"subscribe"}                  {"id":9,"ok":true}   // then events stream
// events
{"ev":"state","state":{...}}               // full snapshot, sent right after subscribe
{"ev":"patch","patch":{...}}               // RFC 7386 JSON merge patch, coalesced <= 30 Hz
{"ev":"toast","level":"warn","text":"..."} // user-visible message
```

Ops (all return `{ok:false,error:"..."}` on failure):

| op | args | notes |
|---|---|---|
| `intent` | `intent` (§5.2) | the console |
| `get_state`, `subscribe` | — | |
| `refresh` | — | same as `Engine.refresh()` (what `lite show refresh` does today) |
| `set_art_mode` | `mode: det|paint|ai` | maps to `Engine.set_mode` (`deterministic|paint|ai`) |
| `fixtures.list/discover/upsert/remove/test` | fixture / id | `test` flashes the fixture briefly |
| `groups.list/upsert/remove` | group | |
| `banks.get/set` | `bank`, `channel`, `group_id` | |
| `takes.list/get/delete/rename/export/set_flag` | take id / flag | flag e.g. `training: true` |
| `takes.marker.add/remove` | take id, `p_ms`, `label` | markers like DROP |
| `rec.arm/start/stop`, `play.start/stop/seek` | take id / mode / pos | §5.4 |
| `take.nudge` | take id, `ms` | stored in the take header |
| `board.pairing` | — | returns key + port for the pairing card (M6) |

State shape (TypeScript-style; schema is authoritative):

```ts
type State = {
  now: { track_id: string, title: string, artist: string, album: string, playing: boolean,
         pos_ms: number|null, dur_ms: number|null, art_url: string|null },
  who: "auto" | "board",          // WHO DRIVES (== Engine.enabled: auto=true, board=false)
  art_mode: "det" | "paint" | "ai",
  bank: "A" | "B" | "C",  fader_fn: "level" | "hue" | "sat",
  master: number, blackout: boolean,
  channels: { bank: string, ch: number, group_id: string|null, level: number, hue: number, sat: number,
              flash: boolean, engaged: boolean }[],     // 24 entries (3 banks x 8)
  last_touched: { bank: string, ch: number } | null,
  room: { fixture_id: string, x: number, y: number, hex: string }[],
  cues: { id: number, name: string }[], palette: string[],     // 8 each
  rec:  { state: "idle"|"armed"|"recording", take_id: string|null, elapsed_ms: number, events: number },
  play: { state: "idle"|"playing", take_id: string|null, pos_ms: number },
  health: { atv: "up"|"down", lifx: { found: number, total: number }, board: "none"|"virtual"|"online"|"offline" },
}
```

### 5.2 Intents (identical for UI, board, replay, CLI)

Each intent has a `src` (`ui|board|replay|cli`) added by the receiver, not the sender.

| intent | fields | meaning |
|---|---|---|
| `fader` | `ch` 1-8 or `"master"`, `v` 0..1 | raw fader position (see pickup, §5.3) |
| `flash` | `ch`, `down` bool | momentary full-level while held |
| `assign` | `ch`, `group_id` | remap a channel in the current bank |
| `bank` | `bank` A/B/C | |
| `fader_fn` | `fn` level/hue/sat | |
| `hue` / `sat` | `delta` (detents) or `v` | acts on `last_touched` channel |
| `palette_apply` / `palette_store` | `i` 0-7 | apply swatch to last-touched channel / store current color |
| `cue_recall` / `cue_store` | `i` 0-7 | |
| `blackout` | `on` bool | |
| `who` | `who` auto/board | |

Unknown intents are rejected with an error; they are never silently dropped.

### 5.3 Console semantics (implement as pure functions in `console.py`; unit-test every rule)

- **Channel state:** each of the 24 channels (3 banks x 8) has `level`, `hue`, `sat`, bound to one group.
- **Group -> fixtures:** a fixture may be in several groups. For each fixture: **level = max (HTP)** across
  the contributing groups' levels; **hue/sat = last touched wins (LTP)**. Then multiply by master; blackout
  forces 0, except that a held flash punches through it (changed by the user 2026-10-16, DECISIONS.md).
- **Flash:** while down, the group's level is 1.0 at the channel's hue/sat; on release it returns to the fader
  level. Flash ignores the fader but respects master, safety caps and the strobe limit (§8).
- **Pickup (required for the physical board):** faders are not motorized. When bank or `fader_fn` changes, each
  channel becomes `engaged=false`; the controller's raw position is ignored until it crosses (or comes within
  0.02 of) the stored value, then `engaged=true` and `v` takes effect. UI faders are treated as engaged
  immediately (drag sets the value). The UI shows a ghost marker at the stored value for disengaged faders.
- **Hue/sat:** act on `last_touched` (the last channel whose fader/flash/ASSIGN was used). Palette apply sets
  hue/sat on it; palette store saves its current color to swatch `i`.
- **Cues:** a cue is a snapshot of all 24 channels' `level/hue/sat` (not master, not who/bank). Store with
  `cue_store`, recall instantly (`fade_ms` configurable, default 0). Names editable. Persist in `console.json`.
- **AUTO vs BOARD (`who`):**
  - `auto`: the album-art painter drives fixtures exactly as today; console state is displayed but not applied.
  - `board`: set `Engine.enabled=False` (the existing mechanism; `/agent` and `/lights/set` already do this)
    and drive fixtures from console state. On switch to board, **initialize each channel's hue/sat from the
    last art colors** so there is no color jump. On switch to auto, call `Engine.refresh()`.
  - Fixtures in no group hold their last state while in `board`. ⚠ Confirm this with the user in review.
- **Output scheduling:** coalesce to the newest value per fixture. LIFX at most **20 messages/s per bulb**
  (⚠ confirm against LIFX LAN docs and measure); virtual outputs 60 Hz. Never queue a backlog; drop old values.
- **Latency instrumentation:** log `t_intent_received` and `t_output_sent` per fixture (debug level) so
  latency can be measured later.

### 5.4 Takes (recordings)

One file per take: `~/.chroma/takes/<YYYYMMDD-HHMMSS>_<slug>.jsonl`. First line is the header, then events.

```jsonl
{"v":1,"id":"20261009-214800","track_id":"<NowPlaying.track_id()>","title":"Older","artist":"Sasha Alex Sloan",
 "album":"Loser - EP","dur_ms":205000,"fixtures_rev":3,"nudge_ms":0,"training":true,"markers":[{"p":72000,"label":"DROP"}]}
{"p":41230,"w":1791600000123,"i":{"intent":"fader","ch":3,"v":0.62},"g":"playbill"}
{"p":41230,"w":1791600000123,"i":{"intent":"flash","ch":3,"down":true}}
```

`p` = song position in ms, `w` = wall-clock ms, `i` = the intent exactly as received (post-pickup values), `g` =
resolved group id (informational). **Record intents, not output frames.** Replay re-feeds the intents (with
`src:"replay"`) through the same `console.apply`, so the result is identical in kind to live control.

- **Record:** `rec.arm` -> waits for a playing track; `rec.start` begins; refuse (toast) if nothing is playing.
  Events are timestamped with the **SongClock** position.
- **Modes:** `REPLACE` overwrites the take's events; `OVERDUB` plays the existing take while recording new
  intents into it (live control overrides playback per channel while touched, LTP); `MERGE` records new intents
  into the take without playing the old ones back.
- **SongClock (`clock.py`):** `pos_ms` from the Apple TV (pyatv `Playing.position`, seconds, and
  `total_time`), interpolated with a monotonic clock while playing, frozen while paused, resynced on each
  update with slew limiting (no backward jumps >50 ms mid-take unless a seek is detected). Note:
  `NowPlaying` in `hub/providers.py` currently has **no** position/duration — add them. ⚠ Position resolution
  and latency are unknown; M1 includes a measurement spike.
- **Replay:** applies `nudge_ms` (take header) and a global offset; seeks if the user scrubs; stops on track change.

### 5.5 Board <-> engine (UDP, state snapshots)

Supersedes the WebSocket protocol in the earlier spec (final-spec §6). Reason: UDP datagrams get lost; an
**edge/delta** protocol would leave flashes stuck or encoders off. A **snapshot** protocol converges.

Board -> engine, JSON, <= 512 bytes, sent when anything changes (<= 60 Hz) and at >= 2 Hz as heartbeat:

```json
{"v":1,"id":"board-1","seq":812,"t":123456,
 "f":[0.73,0.0,0.55,0.0,0.68,0.74,0.30,0.12,0.90],
 "b":"0x00000000A5","e":[120,-14,3],"s":[2,1,0],"mac":"a1b2c3d4e5f60718"}
```

`f` = 8 channel faders + master (0..1), `b` = bitmask of all buttons (bit map in `contracts/board-map.md`: flash
1-8, assign 1-8, palette 1-8, cues 1-8, bank A/B/C, tap, rec, play, stop, blackout), `e` = **absolute** encoder
counts (hue, sat, nudge), `s` = toggle positions (art mode, who, fader fn). The engine **diffs consecutive
snapshots** into intents. Because state is level-triggered, a lost datagram is harmless.

Engine -> board (change + 2 Hz): `{"v":1,"seq":N,"ui":{"bank":"A","labels":[...8],"colors":["#..",...8],
"track":"Older","who":"board","rec":false,"level":[...8]}}`.

Security: `mac` = first 8 bytes of HMAC-SHA256(key, payload-without-mac), hex. Key = 32 random bytes in
`~/.chroma/board.key` (generated on first `board.pairing`). Reject bad MACs and non-increasing `seq` (allow
reordering window of 8). The UDP listener binds only after a key exists; port default **9876** (⚠ configurable).
Offline = no valid packet for 3 s: release all flashes, keep fader values, set `health.board="offline"`.

## 6. Milestones

Each milestone: **Goal / Tasks / AC.** Unless stated, "tests" = pytest (engine), vitest (UI), `cargo test` (Rust glue).

### M0 — Contracts, scaffolding, fakes
**Goal:** a place to work and a way to test without hardware.
Tasks: create `contracts/` with schemas + examples for §5.1, 5.2, 5.4, 5.5, fixtures.json; a pytest that
validates every example. Scaffold `app/` with `create-tauri-app` (Tauri 2, React, TS) + Tailwind. Add
`hub/tests/`, `hub/requirements-dev.txt` (pytest, pytest-asyncio, jsonschema). Write `hub/testing/FakeProvider`
(scripted tracks incl. position, pause/resume, track change).
AC: `pytest` passes and validates all examples; `npm run tauri dev` opens an empty window; FakeProvider
produces a scripted 3-minute track with changing position.

### M1 — chromad skeleton, IPC, SongClock
**Goal:** engine reachable over the Unix socket with live state.
Tasks: `chromad.py` runs the existing `Engine` and the IPC server (`ipc.py`) on `~/.chroma/chromad.sock`
(0600); implement `get_state`, `subscribe`, `refresh`, `set_art_mode`, state patches (<= 30 Hz). Add
`pos_ms/dur_ms` to `NowPlaying` and the `SongClock`. `tools/chromactl.py` (`state`, `watch`, `intent`). Leave
`server.py` and `lite show` untouched. **Spike:** log Apple TV position vs wall clock for >= 5 minutes of real
playback (play, pause, seek, track change); write findings (resolution, latency, drift) to
`docs/mvp/position-spike.md`. This decides how good takes can be.
AC: `chromactl watch` streams patches with FakeProvider; with the real Apple TV the clock follows playback
and `position-spike.md` exists with numbers; existing `lite show` still works.

### M2 — Tauri shell + live read-only Console header
**Goal:** the app shows what is playing and the room.
Tasks (Rust): spawn/supervise `chromad` (dev: `hub/.venv/bin/python hub/chromad.py`; keep the command
configurable), connect to the socket, proxy `invoke('chromad_request')` and emit `chromad://event`; tray icon
(Tauri 2 tray API) with status + menu (Open, Restart engine, Quit); window with Console/Takes/Setup tabs
(Takes/Setup stubs). Tasks (UI): the top bar of the Console mockup: WHO DRIVES and ART MODE segmented
controls (functional), Now Playing card with art + progress, status chips, and the Room view fed by `room`.
Port components from `archive/legacy/chroma/src/components` where they help.
AC: real track/art/progress appear live; ART MODE changes the engine mode; WHO DRIVES toggles
`Engine.enabled`; closing the window leaves lights running; Quit from the tray stops a supervised engine; app
works with FakeProvider when the Apple TV is off.

### M3 — Fixtures, groups, banks, outputs, Setup screen
**Goal:** the model the console runs on.
Tasks: `outputs/base.py` interface (`push(fixture, hsb, fade_ms)`, `capabilities`, `class: slow|fast`),
`outputs/lifx.py` (wrap `LifxController`; AUTO painting must still use it and behave identically),
`outputs/virtual.py`. `fixtures.py`: fixtures/groups/banks, persistence to `fixtures.json`, **import of the
currently discovered LIFX bulbs** (labels from `LifxController.labels()`, positions from `Layout`), 3x8 banks,
default groups from the mockup. IPC ops for fixtures/groups/banks. Setup screen per mockup (table, Discover,
Add, Test, group editor, safety limits, bank grid with drag-and-drop).
AC: edits persist across restart; Discover imports the real bulbs; Test blinks the right bulb; a virtual
fixture appears in the Room view; unit tests for group resolution (HTP/LTP inputs are tested in M4); AUTO
painting regression test passes (same colors as before for a fixed art input).

### M4 — Console, intents, AUTO/BOARD
**Goal:** play the lights from the screen.
Tasks: implement §5.3 in `console.py` as pure logic with exhaustive unit tests (HTP/LTP, master, blackout,
flash, pickup, bank switch, fn switch, cues, palette, strobe limit). Wire `console.apply` to outputs with the
scheduler. Console screen per the **approved layout (presets/cues/palette/room on the left, faders on the
right)**: draggable faders, FLASH press/hold, ASSIGN, hue/sat knobs, palette, cue pads (shift+click or
long-press to store), bank tabs, FADER FUNCTION, master, blackout, group name scribble strips, ghost markers
for disengaged faders. State patches drive the UI; UI sends intents only.
AC: all §5.3 unit tests pass; dragging a UI fader changes a real bulb and a virtual fixture; FLASH holds and
releases; blackout works; switching to BOARD does not cause a color jump; switching back repaints the art
look; strobe limit verified by test; measured intent->output latency logged for virtual (< 20 ms) and LIFX
(report the number).

### M5 — Recorder, replay, Takes screen
**Goal:** record a performance and play it back.
Tasks: `takes.py` (format §5.4, recorder, replayer, REPLACE/OVERDUB/MERGE), IPC ops, Console REC/PLAY/STOP and
bottom activity strip, Takes screen per mockup (library list, timeline canvas with one lane per group, level
curves + flash blocks, ruler, markers added with a key/button, playhead, nudge, mode selector, take details,
training flag, export JSONL).
AC: with FakeProvider + scripted intents, a recorded take validates against the schema and replay reproduces
the intent sequence within +/-20 ms on the virtual output; modes behave per §5.4 with tests; nudge shifts
playback; changing track stops replay; with the real Apple TV, record 3 minutes and replay, reporting the
measured drift (target <= 40 ms after resync; ⚠ may not be achievable depending on M1 findings — report, don't
hide); a take survives app and engine restarts.

### M6 — Board link and simulator
**Goal:** the physical board can be added with no engine changes.
Tasks: `board_link.py` (§5.5): UDP listener, MAC verification, snapshot diff -> intents, engine->board UI
frames, offline detection, `board.pairing` op, a **minimal** Setup "Board" card (not in the mockups — flag for
review) and the Board health chip. `tools/board_sim.py`: scriptable (CLI + Python API), can emit a canned
performance and can replay a take as if it were a board.
AC: **parity test:** the same intent sequence sent via IPC and via `board_sim` yields identical output logs;
with 30% simulated packet loss no flash sticks and state converges within 500 ms; bad MAC and replayed `seq`
are ignored; offline after 3 s releases flashes; bank switch exercises pickup correctly.

### M7 — Packaging and cutover
**Goal:** it runs like an app and replaces the old stack.
Tasks: bundle `chromad` as a Tauri sidecar (PyInstaller or embedded venv; document the choice) ; tray menu
item to install/remove a **LaunchAgent** so the engine runs headless at login and the app attaches; make
`lite show start|stop|status|restart|refresh` delegate to chromad (keep the CLI names and output style);
update READMEs. Then, **with the user's explicit approval only:** retire the hub Music page (`hub/web/index.html`,
`app.js`, `style.css` and its routes) and the old Electron app (`archive/legacy/chroma/`). **Do not remove `hub/web/lights.html`,
`hub/web/remote.html`, `hub/lights_agent.py` or their routes** (`/agent*`, `/lights*`, `/scenes*`, `/tv*`): that would
remove the user's Lights agent and TV Remote with no replacement. If `server.py` must stay running for them,
say so in the report and leave it running alongside chromad (⚠ they both drive LIFX; keep `Engine.enabled`
semantics consistent so they don't fight).
AC: fresh login -> engine running with no window open; the app attaches and shows live state; every `lite
show` subcommand works; `refresh` works through the socket; a clean checkout builds with documented steps.
Out of scope: notarization/signing (list what would be needed).

## 7. Testing and fakes

- Everything must be testable **without** the Apple TV, bulbs or board: `FakeProvider`, `VirtualOutput`,
  `board_sim`, and a fake clock for `SongClock`.
- Pure logic (`console.py`, `takes.py`, merge/replay) gets table-driven unit tests; the IPC layer gets
  integration tests over a temp socket; UI gets component tests for the Console and Takes screens.
- Keep a `docs/mvp/DEMO.md` with the exact commands to demo each milestone's AC.

## 8. Safety (requirements)

1. **Flash/strobe cap:** the combined on/off toggle rate of any fixture must not exceed **3 Hz** by default,
   enforced in the output scheduler for every source (UI, board, replay, future AI). The cap is configurable
   in Setup but never exceeds 3 Hz via any AI-facing path.
2. **Level caps per group** (Setup "Max level") apply after master; they cannot be exceeded by flash.
3. **Hardware protections are not software's job.** Current caps and thermal cutoffs for future pixel bars
   live in the fixture hardware; do not weaken or bypass them. Software limits are a second layer only.
4. **Fail dark-safe:** if chromad loses all controllers, fixtures hold their last state; if it crashes, they
   hold (LIFX/WLED keep their last color). Do not add behavior that drives a fixture to full brightness on
   error.
5. **Network:** the IPC socket is `0600`; the board UDP listener is off until a key exists and rejects
   unauthenticated packets; do not expose any new unauthenticated TCP port. (Existing `server.py` is
   unauthenticated on the LAN — it is retired in M7.)

## 9. Out of scope (phase 2+)

Audio analysis/beat features, AI performance generation and training, WLED output and Playbill bars, the
physical board's firmware, Linux/Pi packaging, notarized distribution, multi-user.

## 10. Open items — defaults to assume (do not stall on these)

Use these defaults unless the user says otherwise. Record any default you rely on in `docs/mvp/DECISIONS.md`
with the date, so the user can overrule it later.

| Open item | Default |
|---|---|
| In `board` mode, fixtures that are in no group | Hold their last state (do not follow the art). |
| Cue recall | Instant (`fade_ms = 0`), configurable per cue later. |
| Board UDP port / enablement | Port **9876**; listener stays off until `board.pairing` creates a key. |
| Hardware layout (cues on the right in `assets/lighting-board-proposal.svg`) | Not a software concern; the user will decide separately. Do not edit hardware SVGs. |
| Real-device checks (Apple TV position spike, real bulbs, real board) | **Do not run them autonomously** (see "Autonomy and hardware" in the kickoff). Build tools and a checklist; mark those AC "pending hardware". |
| Deleting anything at M7 | **Never delete without explicit user approval.** The Lights/Remote pages and light agent stay regardless. Stop and ask about the Music page and `archive/`. |
