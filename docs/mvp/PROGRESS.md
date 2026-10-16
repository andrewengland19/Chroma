# CHROMA Console MVP — progress

Durable memory for the build. On restart: re-read `BUILD_ORDER.md`, this file and `DECISIONS.md`, then continue
at the first milestone that is not **done**. AC status is one of: **pass** (observed), **pending-hardware**
(needs the user's devices; see `HARDWARE_CHECKLIST.md`), **fail**.

Plan: `/Users/andy/.claude/plans/pasted-content-id-0494-you-are-snoopy-whale.md` (approved 2026-10-11).
Worktree: `/Users/andy/Code/Chroma-app` (venv `hub/.venv`). Merges go into `/Users/andy/Code/Chroma` main with
`--no-ff`. Never push.

| Milestone | Branch | Status |
|---|---|---|
| M0 contracts, scaffolding, fakes | `app/m0-contracts` | **done** — merged 072a395 |
| M1 chromad, IPC, SongClock | `app/m1-chromad` | **done** — merged c4ceaad (pending-hardware: H1.1–H1.3) |
| M2 Tauri shell + Console header | `app/m2-shell` | **done** — merged 3af31cf (pending-hardware: H2.1–H2.4) |
| M3 fixtures, groups, banks, Setup | `app/m3-fixtures` | **done** — merged 8b29256 (pending-hardware: H3.1–H3.2) |
| M4 console, intents, AUTO/BOARD | `app/m4-console` | **in progress** — rebased onto 79de99f |
| M5 recorder, replay, Takes | `app/m5-takes` | — |
| M6 board link + simulator | `app/m6-board` | — |
| M7 packaging + cutover | `app/m7-cutover` | — |

## Hand-off for the next session (start here)

Status on 2026-10-14: **M0–M3 done and merged into local main (8b29256)**; next is **M4 (console, intents,
AUTO/BOARD)** on branch `app/m4-console` (already created from 8b29256 in the worktree; it only holds this note). Hardware checks are collected in `HARDWARE_CHECKLIST.md` (none run yet).

How the build has been run (keep doing it this way):
- Work in the worktree `/Users/andy/Code/Chroma-app` (its own `hub/.venv`; `app/node_modules`). Branch per milestone
  from **local** main; small commits; `/code-review high` before merging; merge into `/Users/andy/Code/Chroma` with
  `git -C /Users/andy/Code/Chroma merge --no-ff app/mN-…` only after checking that checkout is clean and on main.
  Never push. After a merge: pytest in the user's checkout with the worktree interpreter, the full
  `tools/test_all.sh` in a temporary **detached** worktree at main (`CARGO_TARGET_DIR` shared), `lite show status`.
- Tests never touch devices: `hub/tests/conftest.py` sandboxes HOME and blocks lifxlan/pyatv/Keychain. Use the fakes
  in `hub/testing/` and `chromad --fake`. Keep Unix socket paths short (`mktemp -d -t cx`; macOS limit 104 bytes).
- UI fidelity: `npx vite --mode mock --port 1430` in `app/`, then `hub/.venv/bin/python tools/ui_compare.py <screen>
  <outdir> --region …` (headless Chrome at 2x next to the mockup). Mock scenarios: `?mock=mockup-console|mockup-setup|
  live|empty`; the mockup data lives in `contracts/examples/{state,fixtures}/ok-mockup*.json`.
- Native app: `CHROMA_DEBUG_IPC=1 CHROMA_SELFTEST=close-quit npm run tauri dev` (debug builds) proves UI → Rust →
  chromad and the close/quit lifecycle without screen access. The user does not grant Screen Recording; ask them for
  screenshots in the batched hardware request instead.
- Contracts first: change `contracts/*.schema.json` + examples, `npm --prefix app run gen:types`, then code.

M4 starting points: `hub/console.py` has the data model; intents arrive in `daemon.apply_intent` (only `who` today);
outputs are `self.lifx_out` / `self.virtual`; per-fixture limits come from `FixtureStore.limits()` (D8); the strobe
definition is D7; board seeding is D2; `LifxOutput.forget_power()` should be called when switching to BOARD.

---

## M0 — contracts, scaffolding, fakes

Started 2026-10-11.

**Done**
- Worktree `/Users/andy/Code/Chroma-app` from local main @ a8d3142; venv `hub/.venv` (Python 3.13, pyatv 0.18.0)
  with `hub/requirements.txt` + new `hub/requirements-dev.txt` (pytest, pytest-asyncio, jsonschema, httpx).
- **Regression net before any engine change** (golden values captured from a8d3142):
  `hub/tests/test_art_paint_regression.py` (19), `test_server_routes.py` (11, incl. every Pass 5 route and page),
  `test_showctl.py` (13). Tests run with `HOME` sandboxed in `hub/tests/conftest.py`; real LIFX discovery,
  `lifxlan.Light`, pyatv scan/connect/pair and the Keychain are blocked. Verified the real `~/.chroma` was not
  written during test runs.
- `hub/testing/`: `FakeClock`, `FakeProvider` (scripted tracks, whole-second pyatv-style position plus a precise
  MRP-style anchor, pause/resume/seek/track change, push + device listeners), `FakeLight`/`FakeLifxLAN`,
  `make_art`.
- `contracts/`: 12 schemas (+ `board-map.md`, README), 76 example files (ok + bad for every contract);
  `hub/tests/test_contracts.py` validates them and checks ops/intents match the spec.
- `app/`: Tauri 2 + React 19 + TS + Vite 8 + Tailwind v4 + vitest; `src/contracts.ts` generated from the
  schemas (`npm run gen:types`, `gen:types:check`). Rust crate `chroma` with a placeholder test.
- `tools/test_all.sh` runs every suite.

**AC**
| AC | Status | Evidence |
|---|---|---|
| `pytest` passes and validates all examples | **pass** | `tools/test_all.sh`: 150 passed (contracts 101 incl. 26 bad examples rejected for the intended reason) |
| `npm run tauri dev` opens an empty window | **pass** | dev binary `target/debug/chroma` (pid 45292) owned an on-screen 1200×750 window per `CGWindowListCopyWindowInfo`. A screenshot was not possible: macOS Screen Recording permission for the Claude app is not granted. |
| FakeProvider produces a scripted 3-minute track with changing position | **pass** | `test_fake_provider.py` (position at 30/60/71/81/100/110/120 s, pause, resume, seek, auto-advance, Engine push path) |

**Merged** into main as 072a395 (2026-10-11). Re-test on main: pytest in the user's checkout 150 passed; full
`tools/test_all.sh` in a detached worktree at 072a395 passed; `lite show status` → running (pid 22052), lights 5.

**Deviations / notes**
- Pre-existing bug fixed (found by the M0 code review): `showctl.start()` checked `os.kill(pid, 0)`, which a
  dead-but-unreaped child still answers, so a hub that crashed within the 1 s grace period was reported as
  "started". It now checks `proc.poll()`; `test_start_reports_immediate_failure` covers it without auto-reaping.
- Code review (high): 8 findings, all fixed — showctl start-failure detection, FakeProvider empty-playlist skip
  and end-of-track "playing" flag, test_all.sh summary under pipefail, `vite.config.ts` now type-checked, unused
  imports, hue range is now [0, 360), pytest warning filter narrowed to the one colorthief/Pillow deprecation.

---

## M1 — chromad, IPC, SongClock

Started 2026-10-11.

**Done**
- `hub/paths.py` + `CHROMA_HOME` in config/layout/scenes/engine/providers/showctl (unset → identical paths;
  `test_paths_and_seams.py` checks both). Engine seams `provider_factory=`, `lifx=`, `np_listeners`
  (defaults unchanged; the M0 regression suite passed before and after).
- `NowPlaying.pos_ms/dur_ms`; `AppleTVProvider.raw_anchor()` reads MRP's float `elapsedTime` +
  `elapsedTimeTimestamp` + `playbackRate` (pyatv 0.18 truncates `Playing.position` to whole seconds).
- `hub/clock.py` SongClock: precise anchor samples, whole-second samples as `[p, p+1000)` intervals, seeks
  (>= 1.5 s) snap + notify, backward corrections <= 50 ms then slewed at 0.9x, pause freeze, track reset,
  clamp to duration, max correction tracked for `sync_err_ms`.
- `hub/ipc.py` (Unix socket 0600, flock single instance, stale-socket cleanup, 1 MiB lines, <= 30 Hz merge
  patches, toasts, slow-consumer resync, intents inline / other ops as tasks), `hub/state.py`,
  `hub/console.py` (model only), `hub/daemon.py` + `hub/chromad.py` (`--fake`, `--home`, `--no-lifx`,
  two-engine guard), ops `get_state`, `subscribe`, `refresh`, `set_art_mode`, `intent` (`who` only).
- `tools/chromactl.py` (`state`, `watch`, `intent`, `op`), `tools/position_spike.py` (`record`, `analyze`).

**AC**
| AC | Status | Evidence |
|---|---|---|
| `chromactl watch` streams patches with FakeProvider | **pass** | `test_chromad.py::test_chromactl_watch_streams_patches_from_a_fake_chromad` (real subprocesses); manual smoke run: state, then `{"now":{"pos_ms":…}}` every ~250 ms, `art_mode`/`who` patches on change |
| With the real Apple TV the clock follows playback | **pending-hardware** | HARDWARE_CHECKLIST H1.2 |
| `position-spike.md` exists with numbers | **pending-hardware** | tool built and tested on fake data; real recording = H1.1 |
| Existing `lite show` still works | **pass** (tests) / **pending-hardware** (live restart) | regression suite 205 passed; `lite show status` after merge; `restart`/`refresh` on the live hub = H1.3 |

**Merged** into main as c4ceaad (2026-10-11). Re-test on main: pytest in the user's checkout 205 passed; full
`tools/test_all.sh` in a detached worktree passed; `lite show status` → running (pid 22052).

**Deviations / notes**
- State patches: `null` means "the value is null", never "delete the key" (the State has a fixed shape). Logged
  in DECISIONS.
- Code review (high): 9 findings; 8 fixed (who intent no longer blocks the socket, guard also protects the live
  hub on `~/.chroma`, signed anchor age, raw_anchor tries every instance, room clamp, stop awaits tasks,
  unserialisable results answered, no test fakes in production `--no-lifx`); 1 deferred to M7 (share the PID
  helpers with showctl when it is rewritten).

---

## M2 — Tauri shell + live Console header

Started 2026-10-11.

**Done**
- Rust shell (`app/src-tauri/src`): `config.rs` (dev → fake engine in `~/.chroma/dev`; env overrides),
  `ipc.rs` (NDJSON client, id correlation, event fan-out, 4 MiB line cap), `supervisor.rs` (spawn in own process
  group, backoff 1/2/4/8/15 s, attach to foreign engines, adopt an engine left by an earlier run, Quit stops only
  ours, log rotation), `art.rs` (`chroma-art://`), `lib.rs` (tray: status / Open / Restart engine / Quit; close
  hides; ⌘Q = Quit; `chromad_request`, `engine_status`; debug-only `CHROMA_SELFTEST`).
- UI: store with merge patches, slice selectors, interpolated position; Tauri + mock IPC adapters; 1178 x 691 canvas
  scaled to fit; tabs (Takes/Setup stubs); WHO DRIVES + ART MODE (live); Now Playing (art, title, artist · album,
  play glyph, progress, time); REC / PLAY / STOP / BLACKOUT (rendered, wired in M4/M5); status chips; Room view.
- `tools/ui_compare.py` (headless Chrome at 2x vs the mockup), `docs/mvp/RUNNING.md` (how to open the app).

**AC**
| AC | Status | Evidence |
|---|---|---|
| Real track/art/progress appear live | **pass (fake engine)** / **pending-hardware (Apple TV)** | Native app + fake chromad: UI `get_state` → Rust → chromad ok (`CHROMA_DEBUG_IPC` log); vitest: title, artist · album, art, progress 49.8 %, time 1:42 / 3:25, interpolation 1:42 → 1:45 over 3 s; real ATV = H2.1 |
| ART MODE changes the engine mode | **pass** (UI→op tested in vitest; op→engine in pytest) / **pending-hardware** (click in the native window = H2.2) | `ConsoleScreen.test.tsx`, `test_chromad.py::test_ops_art_mode_who_refresh_and_errors` |
| WHO DRIVES toggles `Engine.enabled` | **pass** / **pending-hardware** (H2.2) | same tests (who → `Engine.enabled` False/True) |
| Closing the window leaves lights running | **pass** | native self-test: `window visible=false`, engine alive + connected after close |
| Quit from the tray stops a supervised engine | **pass** | native self-test: app and its chromad exit, socket removed; cargo test `spawns_supervises_and_stops_only_its_own_child`, `adopts_an_engine_left_behind…`, `attaches_to_an_external_engine_and_leaves_it_running` |
| Works with FakeProvider when the Apple TV is off | **pass** | dev default (`--fake`) |

**Visual comparison (gui-1)**: header, tabs, segmented controls, Now Playing, transport, chips, Room and all panel
frames line up with the mockup within ~1–2 px at 2x (`tools/ui_compare.py console`). Differences: headless Chrome
renders SF slightly wider than the mockup; the Room dots use the engine's real colours; COLOR / CUES / faders /
activity strip are placeholders until M4/M5. Added (flagged): "fake devices" notice and toasts.

**Merged** into main as 3af31cf (2026-10-11). Re-test on main: pytest in the user's checkout 205 passed; full
`tools/test_all.sh` in a detached worktree passed (205 + 15 vitest + 14 cargo); `lite show status` → running.

**Code review (high)**: 8 findings, all fixed (engine adoption after a crash, restart feedback, no-engine message,
log rotation, slice selectors, mock disposal + excluded from production, Tauri listen errors).

---

## M3 — fixtures, groups, banks, outputs, Setup

Started 2026-10-11, finished 2026-10-14.

**Done**
- `hub/fixtures.py`: fixtures (LIFX / virtual / planned WLED; active / not_added / planned), groups (members, UI colour,
  max level, max flash rate <= 3 Hz, idle auto-dim), 3x8 banks; hand validation; atomic `fixtures.json` with `rev`;
  default rig from gui-3; discovery import mapping the real bulbs onto the mockup groups (D5); most restrictive limits
  across groups (D8); `set_limits`, `swap_banks`; corrupt file kept aside instead of crash-looping.
- `hub/outputs/`: `HSB`, `LifxOutput` (direct MAC/IP or IP-only; power on, never off; 1 Hz transient Test pulse),
  `VirtualOutput` (current colour + frame log), `discover_bulbs`.
- `hub/rigsvc.py`: ops `fixtures.list/discover/upsert/remove/test`, `groups.list/upsert/remove/set_limits`,
  `banks.get/set/swap`; bulbs imported automatically when the rig has none; Room + LIFX health from the rig, linked to
  the engine's bulbs by MAC; virtual lights get art colours in AUTO; moving a LIFX fixture updates `layout.json`.
- Console channels follow the bank map; State `fixtures_rev` = rig rev; the UI refetches the rig once per rev.
- Setup screen per gui-3 (fixtures table, group editor + safety sliders, bank grid with drag-and-drop and keys) and a
  minimal **+ Add fixture** form (flagged).

**AC**
| AC | Status | Evidence |
|---|---|---|
| Edits persist across restart | **pass** | `test_rig_ops.py::test_edits_persist_across_a_chromad_restart` (two chromad instances, same home) |
| Discover imports the real bulbs | **pass (fake LAN)** / **pending-hardware** | `test_discover_is_idempotent`, `test_first_start_imports…`; real LAN = H3.1 |
| Test blinks the right bulb | **pass (fake bulbs)** / **pending-hardware** | `test_test_blinks_the_right_bulb…` (only Couch Left gets the waveform); real bulb = H3.1 |
| A virtual fixture appears in the Room view | **pass** | `test_virtual_fixture_appears_in_the_room_with_an_art_colour` |
| Unit tests for group resolution | **pass** | `test_fixtures.py` (members, limits, import mapping, validation) |
| AUTO painting regression (same colours for a fixed art input) | **pass** | M0 goldens unchanged + `test_auto_painting_path_is_unchanged_by_the_rig` |

Native app: the self-test run shows the UI fetching `get_state` and `fixtures.list` through Rust from a fake chromad,
which seeded the rig and imported 8 fake bulbs (5 active, 3 not added).

**Visual comparison (gui-3)**: fixtures table, group editor and bank grid line up with the mockup at 2x
(`tools/ui_compare.py setup`). Deliberate differences: a row "×" (remove, asks to confirm); member dots use the
fixture's real status (planned = grey; the mockup shows green); the flash-rate track is drawn to 10 Hz with everything
above the 3 Hz safety cap hatched and unreachable (the mockup puts the 3 Hz knob ~30 % along).

**Merged** into main as 8b29256 (2026-10-14). Re-test on main: pytest in the user's checkout 251 passed; full
`tools/test_all.sh` in a detached worktree passed (251 + 21 vitest + 14 cargo); `lite show status` → running.

**Code review (high)**: 7 findings, all fixed (MAC linking, corrupt-file recovery, limits-only and atomic-swap ops,
remove confirmation, import retry, non-blocking blink).

**Post-merge fix (2026-10-14, `app/fix-test-blink`)**: "Test on TV Left changed nothing although the bulbs were
discovered" turned out to be the debug app running against the **fake** engine (`config.rs`: debug → `~/.chroma/dev`
+ `--fake`). The discovered bulbs were `hub/testing/fake_lifx.py`'s (TV Left = `192.168.0.31`,
`d0:73:d5:00:00:01`), and H3.1 (real bulbs via `CHROMA_HOME=/tmp/chroma-hw CHROMAD_ARGS=""`) had not been run.
Real-mode hardening: `LifxOutput.blink` now checks power and sends the waveform **with ack** (before this it was a
single fire-and-forget UDP packet, so an unreachable bulb "succeeded"). A switched-off bulb gets "… is switched off",
an unreachable one gets "could not reach …", and success toasts "Blinked …", plus "(fake bulb …)" in fake mode.
Tests: 5 new (blink ack/off/unreachable, fake toast, discover refreshes a moved IP). New hardware checks H3.1b/H3.1c.
Code review (high): 5 findings, all fixed: IP-only fixtures keep the fire-and-forget pulse (lifxlan matches acks by
MAC); acked calls are retried 3x; the toast is "could not reach X at IP (no reply)" with details logged; a double-click
doesn't start a second blink; fakes raise lifxlan's WorkflowException.

---

## M4 — console, intents, AUTO/BOARD

Started 2026-10-14 (branch rebased onto main 79de99f, after the Test-blink fix).

**Contracts audit (no schema change needed)**: every §5.2 intent has a schema def and ok/bad examples
(`contracts/intent.schema.json`, 19 ok + 8 bad). State already carries `master`, `blackout`, `last_touched`,
`cue_active`, `cues[]{id,name,stored}`, `palette[8]` and per-channel `level/hue/sat/flash/engaged`. The ghost marker
is drawn at the stored `level` when `engaged=false`, so the raw board position stays out of State. `console.json` is
`contracts/console.schema.json` (24 look channels, 8 palette, 8 cues with optional `fade_ms`, default 0 per D16).
Cue names go through the existing `cues.rename` op. `contracts.ts` is up to date.

**§5.3 console logic (pure, `hub/console.py` + `hub/oklab.py`)**: `apply(state, intent, src)` handles all 12
console intents (`who` stays with the daemon) and returns effects for the daemon: `("assign", bank, ch, gid)` to
persist in the rig's banks, `("recall", i, fade_ms)`. `resolve(state, members, limits)` → HSB per fixture: HTP level
(flash = 1.0), LTP hue/sat by touch order (cue-recall ties go to the brighter channel), × master, blackout → 0 (beats
flash), then the D8 `max_level` cap (flash can't exceed it). Fixtures in no bound group are absent (they hold, D16).
`seed_from_art` = D2: after it, each fixture keeps its art colour / brightness until one of its channels moves
colour / level. Channels show the OKLab mean hue/sat and the mean brightness. `StrobeLimiter` = D7 per fixture: an
edge is |Δlevel| ≥ 0.10 or an OKLab jump ≥ 0.10 with both ends ≥ 0.10. The budget is `floor(2·hz)` edges per
`n/(2·hz)` s (3 Hz = 6 per 1 s; never above 3 Hz). The newest held value goes out via `poll`/`next_due_ms`.
`to_file`/`from_file` = console.json.
Pickup: the engaged state describes the physical fader. Board moves apply only after reaching (±0.02) or crossing
the stored value. The first sight of a fader that is far away is ignored (no jump on connect). A bank/fn switch
disengages, then re-engages the faders that already match. UI/cue/palette/encoder changes apply at once and
disengage a board fader that no longer matches. With no board seen, everything stays engaged (no ghost markers
without a board). Encoders: 5° / 2 % per detent.
Tests: `hub/tests/test_console.py`, 50 cases (validation, HTP/LTP, master/blackout, caps, flash, pickup incl. hue/sat
functions, encoders, palette, assign, cues, D2 seed, D7 sliding window). `tools/test_all.sh`: 308 + 21 + 14 passed.

**Step 3, wiring (`hub/daemon.py`, `hub/scheduler.py`)**: `apply_intent` → `console.apply` for every non-`who` intent.
`ConsoleError` → IPC error. An unknown `group_id` is rejected before apply. An `assign` effect is persisted with
`store.set_bank`. In BOARD (`Engine.enabled == False`), `resolve` → `OutputScheduler.submit`. In AUTO the console
state changes and is shown, but nothing is pushed.
- `OutputScheduler`: the newest value per fixture, no backlog. LIFX ≥ 50 ms apart (≤ 20 msg/s per bulb), virtual
  60 Hz, then the D7 `StrobeLimiter` (held values are released by the timer). Debug log line per output with
  `t_intent_received` / `t_output_sent`; p50/p95/max per class, logged at shutdown. `step(now)` is synchronous
  (fake-clock tests).
- AUTO→BOARD (`set_who` or the first console intent after an external disable, D3): `seed_from_art` from the art
  painter's last HSBK per bulb (matched by MAC) and the virtual lights' colours. The scheduler is primed with that
  look, so nothing is pushed until something changes (D2). `LifxOutput.forget_power()`. BOARD→AUTO: the scheduler is
  reset and the engine repaints from cached art (X3). The seed runs *before* the first intent is applied, so the move
  is not overwritten.
- **Fix to step 2 (found by the wiring tests)**: after the seed, untouched channels still joined the HTP at their
  seeded art brightness, so pulling LR ALL down did nothing (LR-L / TV held the bulbs up). Seeded levels are
  display/pickup only (D2): only channels moved since the seed take part.
- Room view in BOARD shows what the console last sent to each LIFX bulb (`RigService.live`); virtual lights already
  show their output.
- `console.json`: loaded at start (unreadable → `.bad`, defaults), saved 0.5 s after the last change and at shutdown.
  `cues.rename` op. Who, blackout and flash are not restored.

Tests: `test_board.py` 13 (through chromad with fake bulbs: D2 no push / no jump, UI fader → bulb + virtual, art
colour kept until colour is touched, FLASH hold/release, blackout beats flash and never powers off, AUTO pushes
nothing, back to AUTO repaints, external disable, 20 Hz flash hammering ≤ 6 edges in any 1 s, LIFX ≥ 50 ms apart and
coalesced, D8 cap incl. flash, assign persistence, restart persistence, corrupt console.json),
`test_scheduler.py` 5 (fake clock). `tools/test_all.sh`: 326 + 21 + 14 passed.

**Latency (fake devices, in-process)**: virtual p50 0.08 ms / max 0.08 ms (AC < 20 ms; the test asserts it). LIFX
path to the socket: p50 0.13 ms on single moves; during a 100-moves/s drag p50 4.7 / p95 11.7 / max 12.0 ms. That
includes waiting for the 50 ms slot. The real bulb number is H4.1.

Next: step 5, the Console screen per gui-1 (faders + ghost markers, FLASH, ASSIGN picker, hue/sat knobs, palette, cue
pads, bank tabs, FADER FUNCTION, master, BLACKOUT, scribble strips).
