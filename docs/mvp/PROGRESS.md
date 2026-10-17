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
| M4 console, intents, AUTO/BOARD | `app/m4-console` | **done** — merged b88d544 (pending-hardware: H4.1–H4.2) |
| M5 recorder, replay, Takes | `app/m5-takes` | **done** — merged 14d7f72 (pending-hardware: H5.1–H5.4) |
| M6 board link + simulator | `app/m6-board` | next — branch on main 14d7f72 |
| M7 packaging + cutover | `app/m7-cutover` | — |

## Hand-off for the next session (start here)

Status on 2026-10-16: **M0–M5 done and merged into local main (M5 = 14d7f72)**; next is **M6 (board link +
simulator)** on branch `app/m6-board` (on main 14d7f72 in the worktree; it only holds these notes). Hardware checks:
`HARDWARE_CHECKLIST.md` (none run yet; H5.1–H5.4 added). BLACKOUT decisions made: going dark bypasses the D7 hold
(the edge still counts), and a held FLASH punches through BLACKOUT. No open user decisions.

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
  screenshots in the batched hardware request instead. **Always use a temp home** (`CHROMA_HOME=$(mktemp -d -t cxst)`)
  and run it in the background (macOS has no `timeout`; the self-test quits itself). If port 1420 is taken (the
  user's own `tauri dev` from the main checkout), add `--config '{"build":{"devUrl":"http://localhost:1440",
  "beforeDevCommand":"npm run dev -- --port 1440 --strictPort"}}'` (`npx tauri dev …`). While it runs, drive
  intents with `CHROMA_HOME=<temp> hub/.venv/bin/python tools/chromactl.py intent …`.
- The user runs hardware checks on their own (e.g. on `/tmp/chroma-hw` with `lite show` stopped). Before any
  self-test or merge, check `pgrep -fl chromad.py` and leave the user's engines and apps alone. Note that a merge into
  main hot-reloads the UI of a `tauri dev` running from the main checkout, but its engine keeps the old code until
  they restart. A stale fake engine on `~/.chroma/dev` gets *adopted* by the next fake-mode app, so tell the user to
  stop it rather than testing against old code.
- If the main checkout has uncommitted work of the user's, never commit or discard it without asking (on 2026-10-16
  the user had their hardware SVG edit committed as aa7bc38 and a stray `app/app/` lockfile removed; main was clean).
- Contracts first: change `contracts/*.schema.json` + examples, `npm --prefix app run gen:types`, then code.

M6 starting points: board-sourced intents enter through `daemon.apply_intent(intent, "board")` (pickup in
`console._fader`); takes record them like UI intents (a board move that did not pick up is not recorded). The
parity test (§M6) can reuse `test_takes_daemon.py`'s fake-clock harness (`fake_daemon`, `advance`) and compare
`d.virtual.frames`. `board_sim` replaying a take: read events with `takes.read_take` and send `i` as board snapshots.
`State.health.board` is still always "none".

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

**Step 5, Console screen (gui-1)**: `app/src/components/console/BankPanel.tsx` (BANK tabs, FADER FUNCTION, 8 strips,
MASTER) and `ColorCues.tsx` (COLOR, CUES), wired into `ConsoleScreen`. The top BLACKOUT is live too. Everything
shown comes from the State; controls only send intents.
- Strips: scribble strip = group name from the rig (green mono, "—" when unbound). ASSIGN = select (X2) + group picker
  (D11, incl. None). Fader: drag or click anywhere on the track, arrow keys; fill = the channel's live colour (X5);
  scale 100/50/0, or 360/180/0 in HUE. A disengaged channel shows its cap as a dashed **ghost marker** with a "not
  picked up" tooltip. Value text follows the fader function. FLASH is momentary (pointer down/up/cancel/lost capture),
  lit and glowing while the State says `flash`. Channel number.
- MASTER fader + BLACKOUT, both blackout buttons show the state (filled red when on).
- COLOR: hue/sat knobs (270° arc, 4 px of vertical drag = 1 encoder detent, wheel, arrows) show the last-touched
  channel ("—" when none). Palette swatches: tap = apply, hold 600 ms or shift = store (D12). The swatch matching the
  last-touched colour gets the white ring.
- CUES: tap = recall, shift + tap or hold = store; the active cue is filled; empty cues are dashed.
- Mock engine mirrors the console intents (no pickup/arbitration) so the browser mock responds.
- Visual check (`tools/ui_compare.py console`, 2x headless Chrome): strips, scribble strips, ASSIGN, tracks, caps, values,
  FLASH rings and numbers within about 1 px of gui-1; COLOR and CUES within about 1–2 px. Known differences: headless
  Chrome renders SF slightly wider (the palette hint is set at 6.5 px to stay on one line), and the recording strip
  stays a placeholder until M5.
Tests: `ConsoleControls.test.tsx` 10 (strips + live colours, fader drag + master, FLASH press/release, bank/fn + ghost
markers + hue scale, ASSIGN picker, both BLACKOUTs, knobs + detents + wheel, palette tap/hold, cue recall/store,
engine error toast). `tools/test_all.sh`: 326 + 31 + 14 passed.

**Native self-test** (isolated temp home, port 1440, so the user's running dev app was left alone): the UI fetched
`get_state` + `fixtures.list` through Rust, close hid the window with the engine alive and connected, Quit stopped
the engine it spawned. While it ran, chromactl drove `who board`, `fader ch=1 v=0.3`, `flash ch=8`, `cue_store i=1`
at the same engine: the State showed them, the Room hexes dropped to 30 % brightness on the Living Room bulbs, the
virtual light went to full, and `console.json` was written at shutdown with the cue's look.

**Code review (high)**: 8 findings, 7 fixed, 1 left for the user:
- FLASH held while the bank changes: the strip now releases on unmount (a flash could stay stuck on).
- The ASSIGN button now closes its own picker (it reopened).
- No console push once AUTO resumes outside `set_who` (`_push` drops frames in AUTO; the scheduler is reset when
  AUTO is detected).
- `who` flips Engine.enabled at once (seed included). Only the save/repaint runs in the background, so an intent
  right after BOARD is driven, not seeded over. `test_who_intent_never_blocks_the_connection` now checks this.
- Knob wheel: deltaY 0 is ignored; 40 px of delta = one detent (a mouse notch = 1, trackpads don't flood).
- A HUE fader at the top stays at 359.9° (it wrapped to 0 and the cap jumped).
- Knobs send nothing without a touched channel (it spammed error toasts).
- **Open (user decision):** the D7 limiter can hold BLACKOUT for up to ~1 s after heavy flashing, because going dark
  is an edge too. Letting blackout bypass the limit would let blackout toggling strobe above 3 Hz (§8.1), so it is
  unchanged.
`tools/test_all.sh`: 328 + 34 + 14 passed.

**Merged** into main as b88d544 (2026-10-16). Re-test on main: pytest in the user's checkout 328 passed; full
`tools/test_all.sh` in a detached worktree passed (328 + 34 vitest + 14 cargo); `lite show status` → stopped (it was
already stopped before the merge; not started by the builder).

---

## M5 — recorder, replay, Takes

Started 2026-10-16 (branch `app/m5-takes` on main aa7bc38). First commit: the user's BLACKOUT decision (going dark
bypasses the D7 hold, the edge still counts; DECISIONS 2026-10-16). Later: FLASH punches through BLACKOUT (user).

**Plan**
1. Contracts (mostly in place since M0: take header/event, `takes.*`, `rec.*`, `play.*`, `take.nudge`, State
   `rec`/`play`/`takes_rev`). Additions: header `art_hex` (library thumbnail); State `activity` (lanes of recent
   event spans for the Console strip); response shapes for `takes.list/get/export`; examples; `gen:types`.
2. `hub/takes.py`, pure where possible:
   - `TakeStore`: `$CHROMA_HOME/takes/<id>_<slug>.jsonl`, atomic rewrites, list (headers cached by mtime),
     get/delete (moved to `takes/.deleted/`, never unlinked)/rename/duplicate/set_flag/markers/nudge/export.
   - `resolve_event(before, after, intent)` → D9 `b/c/a/v` + `g` from the console state after apply. Not recorded:
     `who`, `palette_store`, `cue_store` (editing, not performing) and board moves that did not pick up.
   - `Recorder`: SongClock-stamped events, `init` snapshot, D10 modes at stop: REPLACE = new only; MERGE = old + new;
     OVERDUB = old minus each channel's events inside its touch spans (live input + 750 ms) + new, and a flash the
     old take held into a span is released at the span start.
   - `Replayer`: `step(pos_ms)` applies every due event (pos − nudge − global offset), `seek(pos)` rebuilds the
     console from `init` + events up to pos without output, skip predicate for OVERDUB touch spans.
     Replay goes through `console.apply(..., src="replay", at=ev)`: the resolved `b/c/a/v` targets the channel, so
     replay never depends on the live bank, fader function or last-touched channel. Replay skips `bank`,
     `fader_fn`, `select` and `assign` (they would move the user's view / rewrite the rig).
3. Daemon: recorder hook in `apply_intent` (src ≠ replay), replay loop on the SongClock (sleeps to the next due
   event, ≤ 50 ms), stop on track change, follow seeks, REC/PLAY switch to BOARD, `rec.arm` starts on play,
   recording stops (and saves) on track change and at shutdown. Ops per §5.1.
4. UI: Console REC/PLAY/STOP + activity strip (gui-1 bottom); Takes screen (gui-2): library + search, transport,
   mode, nudge, loop, ruler + markers (M key / button), one lane per group (level curve + flash blocks), playhead,
   details, training toggle, export/duplicate/delete. `tools/ui_compare.py takes`.
5. AC tests: schema-valid recorded take; ±20 ms replay on the virtual output (fake clock + `scheduler.step`);
   mode tests; nudge; track change stops replay; restart survival. Real Apple TV drift = H5.

**Done**
- Contracts: take header `art_hex`; State `activity` (Console strip lanes); response shapes `takes_list`, `take_full`,
  `take_export` (+ `track_id` in summaries); examples; `contracts.ts` regenerated (DECISIONS X7).
- `hub/takes.py`: `TakeStore` (atomic rewrites, header cache, delete → `takes/.deleted/` without overwriting),
  `resolve_event` (D9), `Recorder` (REPLACE / MERGE / OVERDUB punch-in, D10), `Replayer`, `activity_lanes`,
  `TakeService` (recording, playback on the SongClock, all §5.1 take ops).
- `console.apply(..., src="replay", at=event)` → `apply_resolved`: replay targets the recorded bank/channel and never
  moves the live bank, fader function, selection or the rig's bindings; `load_init` / `snapshot_init`.
- Daemon: recorder hook in `apply_intent` (src ≠ replay), REC/PLAY switch to BOARD (toast), `rec.arm` starts with
  the song, track change stops recording (saved) and replay, seeks rebuild the replayed look, recording is saved at
  shutdown, `--replay-offset-ms` (global offset), `AppleTVProvider.seek` (timeline scrub; pending-hardware).
- UI: Console REC/PLAY/STOP wired (REC lit/armed/idle, PLAY = newest take of the song) and the activity strip;
  Takes screen (library + search, REC into the selected take with the mode, nudge ±5 ms / shift ±1, loop, time,
  ruler, markers via M / double-click / flag click to remove, lanes with level curves and flash blocks, playhead,
  click-to-seek, details, rename, training flag, duplicate, delete, export → `~/Downloads` via a native command).

**AC**
| AC | Status | Evidence |
|---|---|---|
| A recorded take validates against the schema | **pass** | `test_takes_daemon.py::test_a_recorded_take_validates_and_replays_within_20ms…`; native self-test take file validated |
| Replay reproduces the intents within ±20 ms on the virtual output | **pass** | fake clock + `scheduler.step`: every frame within 1 ms (the 1 ms step); real asyncio loop: 1.3–4.1 ms (`test_real_time_replay…`). A 50 ms offset makes the test fail. |
| Modes behave per §5.4 / D10 | **pass** | `test_takes.py` (REPLACE, MERGE, OVERDUB spans, flash release), `test_takes_daemon.py` (overdub live override + old take resumes after 750 ms, merge/replace into a take, live flash kept) |
| Nudge shifts playback | **pass** | `test_nudge_shifts_playback_later` (+200 ms → 320 ms for a 120 ms event); recording into a nudged take is stored in take time |
| Changing track stops replay | **pass** | `test_track_change_stops_replay…` (and recording stops + saves) |
| Real Apple TV: 3 min record + replay, drift | **pending-hardware** | H5.1 |
| A take survives app and engine restarts | **pass (engine)** / **pending-hardware (app, real)** | `test_take_ops_and_a_take_survives_a_restart`; H5.3 |

**Native self-test** (temp home, port 1440; the user's `tauri dev` on 1420 and `/tmp/chroma-hw` engine untouched): the
UI fetched `get_state` and `takes.list` through Rust (again after `takes_rev` moved). chromactl drove `rec.start` →
BOARD, 4 intents, `rec.stop` (take file valid, 4 events), `play.seek 0`, `play.start` (State `play` playing,
activity source `play`), `play.stop`. Close hid the window with the engine alive; Quit stopped it.

**Visual comparison** (`tools/ui_compare.py takes`, console strip region): layout within ~1–2 px of gui-2 and of the
gui-1 strip. Known differences: headless Chrome SF is slightly wider; the footer says "35m recorded" (sum of the six
mock takes' lengths; the mockup's "1h 02m" is not derivable); FX lane says "level · color" (the mockup says "flash
only" over a level curve; the subtitle is derived from the events); the lane curves and strip bars come from
generated mock events.

**Code review (high)**: 8 findings, all fixed with regression tests (each fails on the pre-fix code): OVERDUB released
the performer's own live FLASH; init was snapshotted before the D2 seed; events recorded into a nudged take were not
in take time; REC in MERGE/REPLACE left a playback running; a refused `rec.arm` stayed armed; delete could overwrite
an earlier deleted copy; groups-touched recount per State build; lane paths rebuilt every playhead tick.
`tools/test_all.sh`: 369 pytest + 43 vitest + 15 cargo passed. One flake seen once under load:
`test_chromactl_watch_streams_patches_from_a_fake_chromad` (passed 4/4 alone and in the next full runs; it now prints
the patches if it fails again).

**Deviations / notes (flagged for review)**
- Not recorded: `who`, `palette_store`, `cue_store` (editing, not performing), board moves that did not pick up.
  Recorded but not replayed: `bank`, `fader_fn`, select, `assign` (would move the user's view / rewrite the rig).
  A replayed cue recall uses the cue's *current* contents.
- REC and PLAY switch to BOARD (toast). A take only plays / records with its own song (track id, else title +
  artist). PLAY on the Console = newest take of the song now playing. STOP stops both.
- Library red dot = the take being recorded or played.
- Export writes to `~/Downloads` (no save dialog: that would need `tauri-plugin-dialog`, a new dependency).
- Recording in progress is kept in memory and saved on STOP / track change / engine shutdown; an engine crash
  mid-take loses that pass.

**Merged** into main as 14d7f72 (2026-10-16, with the user's OK while their `tauri dev` ran from main: it rebuilt and
restarted the app; their engine on `/tmp/chroma-hw` kept running the pre-M5 code until restarted). Re-test on main:
pytest in the user's checkout 369 passed; full `tools/test_all.sh` in a detached worktree passed (369 + 43 vitest +
15 cargo); `lite show status` → stopped (already stopped before the merge; not started by the builder).

