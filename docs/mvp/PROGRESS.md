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
| M2 Tauri shell + Console header | `app/m2-shell` | in progress |
| M3 fixtures, groups, banks, Setup | `app/m3-fixtures` | — |
| M4 console, intents, AUTO/BOARD | `app/m4-console` | — |
| M5 recorder, replay, Takes | `app/m5-takes` | — |
| M6 board link + simulator | `app/m6-board` | — |
| M7 packaging + cutover | `app/m7-cutover` | — |

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
