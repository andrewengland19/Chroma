# CHROMA Console MVP — decisions

Every decision the builder made, dated, so the user can overrule it. The plan (questions Q1–Q15 with recommended
answers) was approved as written on 2026-10-11; those answers are recorded here. Newest entries at the bottom.

## Approved plan answers (2026-10-11)

| # | Decision |
|---|---|
| D1 (Q1) | `CHROMA_HOME` env var (default `~/.chroma`) is honoured by `config.py`, `layout.py`, `scenes.py`, `engine.py`, `providers.py`, `showctl.py`. Unset = identical behaviour. `chromad --fake` uses FakeProvider + FakeLifx; dev and tests never touch real devices or the live `~/.chroma`. |
| D2 (Q2) | Switching to BOARD pushes nothing. Each fixture's own art colour is its initial LTP colour source and its art brightness is its level until a channel containing it moves level. Channels are seeded for display/pickup: hue/sat = OKLab mean of the group's art colours, level = mean art brightness. |
| D3 (Q3) | At M7 chromad hosts `server.py`'s routes in-process on `0.0.0.0:8765` (existing exposure). External `enabled=False` (`/agent`, `/lights/set`, `/scenes/recall`) shows `who=board` but the console pushes nothing until a console intent arrives. |
| D4 (Q4) | M7: keep `hub/web/style.css` (Lights/Remote need it); `/` redirects to `/lights.html` instead of deleting `index.html`. `app.js`, Music-only routes and `chroma/` are deleted only on explicit approval at M7. |
| D5 (Q5) | Mockup fixtures map to real bulbs: TV Left→TV Left, TV Right→TV Right, Couch L→Couch Left, Couch R→Right couch, Lamp→Washer (lr-all + accent). Real labels are displayed. |
| D6 (Q6) | Idle auto-dim (in gui-3, not in the spec): per group, BOARD only, after N min with no controller input fade the group to 30 % of its level over 10 s; any intent restores. Never brightens. |
| D7 (Q7) | Strobe edge = abs(delta level) >= 0.10, or OKLab colour jump >= 0.10 while level >= 0.10, measured after caps and master. Max 6 edges in any sliding 1 s per fixture (= 3 Hz on/off). A blocked value is held; the newest value is applied as soon as allowed. Setup can only lower the cap below 3 Hz. |
| D8 (Q8) | A fixture in several groups uses the minimum `max_level` and minimum flash rate across all its groups. |
| D9 (Q9) | Take events carry resolved `b` (bank), `c` (channel), `a` (attribute), `v` (absolute value) beside the verbatim intent `i`; header gains `init` (console snapshot), `sync_err_ms`, `name`. Board packets gain `boot` (per-boot nonce); engine→board frames carry `mac`. |
| D10 (Q10) | REPLACE = new events replace all; MERGE = add without removing; OVERDUB = old take plays, a channel is "touched" while it got live input in the last 750 ms, and during touch that channel's old events in that span are replaced (punch-in). |
| D11 (Q11) | Board ASSIGN press = select channel (`assign` without `group_id`); hold ASSIGN + turn hue encoder = cycle its group. UI ASSIGN opens a group picker. |
| D12 (Q12) | Board: hold >= 600 ms on palette/cue = store. TAP button reserved (no intent in MVP). |
| D13 (Q13) | After each merge: pytest in the user's checkout with the worktree's interpreter; vitest + cargo in a temporary detached worktree at main's HEAD; `lite show status` (read-only). `restart`/`refresh` go on the hardware checklist. |
| D14 (Q14) | Milestones merge with hardware AC marked pending-hardware; one batched hardware request per milestone. |
| D15 (Q15) | Board-sourced intents use pickup; UI intents engage immediately. LIFX in BOARD: level 0 = brightness 0 (never power-off); bulbs are powered on only when a level > 0 is sent. |
| D16 | §10 defaults accepted: unassigned fixtures hold in BOARD; cue recall instant; UDP 9876 off until `board.pairing`; hardware SVGs untouched; real-device checks never autonomous; nothing deleted at M7 without explicit approval. |

## Deviations from BUILD_ORDER.md (approved 2026-10-11)

| # | Deviation |
|---|---|
| X1 | Schema additions: `room[].label`; `state.fixtures_rev`, `takes_rev`, `cue_active`; take header `init`, `sync_err_ms`, `name`; take events `b/c/a/v`; board packet `boot`; engine→board `mac`; ops `takes.duplicate`, `cues.rename`, `play.start{loop}`. |
| X2 | `assign` without `group_id` = select the channel (sets `last_touched`). |
| X3 | Switching to AUTO calls `Engine.set_enabled(True)` (instant repaint from cached art); `Engine.refresh()` only when no art is cached. |
| X4 | Board snapshots older than the newest applied are never applied; within 8 of the newest they are dropped quietly, older ones count as replays. |
| X5 | Fader fill = channel's live colour; Takes lanes and bank bars = group UI colour. |
| X6 | `now.pos_ms` patched at <= 4 Hz; the UI interpolates. |
| X7 | (M5, builder) Schema additions: take header `art_hex`; State `activity` {source, dur_ms, lanes[{group_id, color, spans}]} for the Console strip; response shapes `takes_list`, `take_full`, `take_export`. |

## Builder decisions (log)

- 2026-10-11 — Python 3.13 (miniforge, same as the live venv) for the worktree venv; pyatv 0.18.0 matches the live venv.
- 2026-10-11 — Dev/test deps in `hub/requirements-dev.txt`: pytest, pytest-asyncio, jsonschema, httpx (FastAPI TestClient). None are runtime deps.
- 2026-10-11 — Fixed a pre-existing `showctl.start()` bug (dead child reported as "started"): checks `proc.poll()` instead of `os.kill(pid, 0)`. Output strings unchanged.
- 2026-10-11 — Hue is `[0, 360)` everywhere (schema `exclusiveMaximum: 360`); console code normalises with `% 360`.
- 2026-10-11 — Tailwind v4 (`@tailwindcss/vite`), React 19, Vite 8, TypeScript 6 (create-tauri-app 4.7.5 defaults). Tests import from `vitest` explicitly (no globals).
- 2026-10-11 — Contract TS types are generated into `app/src/contracts.ts` with fixed-length arrays emitted as plain arrays (`ignoreMinAndMaxItems`); the schema still enforces the lengths.
- 2026-10-11 — User will not grant Screen Recording; he provides screenshots of the native app manually. Native-window checks: the window's existence/size is verified via `CGWindowListCopyWindowInfo` (no permission needed); visual checks against the mockups run in the built-in browser with the mock IPC; a "screenshot of the Tauri window" item goes into each UI milestone's batched request (HARDWARE_CHECKLIST.md).
- 2026-10-11 — IPC merge patches: the State never removes keys, so `null` in a patch means the value is null (not "delete"). Python `apply_merge_patch` and the UI store follow this.
- 2026-10-11 — chromad layout: `hub/chromad.py` (entrypoint, sets CHROMA_HOME before imports) + `hub/daemon.py` (wiring). `intent` and `get_state` are handled in arrival order on each connection; `who` is applied in the background (FIFO) so a slow repaint/refresh never stalls the console.
- 2026-10-11 — SongClock uses MRP's raw anchor via private pyatv state (`metadata.main_instance.psm.playing`), guarded; falls back to whole-second samples treated as intervals. The spike (H1.1) decides whether the private path is worth keeping.
- 2026-10-11 — A real chromad refuses to start while server.py runs on the same home, or on `~/.chroma` unless `--no-lifx` (`--allow-alongside` overrides).
- 2026-10-11 — Socket paths are limited to 103 bytes (macOS); dev/test homes use short `mktemp -d` paths.
- 2026-10-11 — Dev builds of the app default to a **fake** engine in `~/.chroma/dev` (`--fake`); release builds default to the real engine in `~/.chroma`. Overridable with CHROMA_HOME / CHROMAD_ARGS / CHROMAD_CMD / CHROMA_SUPERVISE (docs/mvp/RUNNING.md).
- 2026-10-11 — The UI is laid out on a fixed 1178 x 691 canvas (the mockups' logical window size, measured from the 2400 x 1500 PNGs) and scaled to fit the window with CSS zoom, so proportions match the mockups at any window size. Default window 1472 x 864 (= 1.25x).
- 2026-10-11 — chromad spawned by the app runs in its own process group and logs to `$CHROMA_HOME/chromad.log`; closing the window hides it; Quit / ⌘Q stops only an engine the app spawned; an engine started elsewhere is attached to and left running.
- 2026-10-11 — Added (not in the mockup): a small "fake devices" / "connecting to engine…" notice at the top right of the tab row, and toasts at the bottom right. Flagged for review.
- 2026-10-11 — Visual checks: `tools/ui_compare.py` renders the mock UI with headless Chrome (throwaway profile) at 2x and places it next to the mockup; the native window is verified via CGWindowList + a debug-only CHROMA_SELFTEST hook, plus user screenshots.
- 2026-10-14 — Default rig (first start): the gui-3 groups/banks, planned WLED placeholders (Counter strip, Playbill 01–12), one Virtual Light, and the LIFX bulbs found by discovery (Living Room → active with default groups per D5; others "not added yet"). Discovery runs automatically whenever the rig has no LIFX fixtures.
- 2026-10-14 — LIFX fixtures are linked to the engine's bulbs by MAC; the user's fixture name wins over the bulb label on rediscovery. A LIFX fixture may be added by IP only (zero MAC, like lights_agent).
- 2026-10-14 — Virtual lights in AUTO get a deterministic art-region colour at their position (paint colours in PAINT mode); AI colours are not applied to them.
- 2026-10-14 — Setup "Test" = LIFX transient waveform, two 1 Hz pulses to 15 % brightness, bulb returns to its own colour; power state untouched. Virtual: the same pattern in-app.
- 2026-10-14 — Added ops `groups.set_limits` (limits only) and `banks.swap` (atomic) to the contract, so the UI never re-sends stale member lists or splits a swap into two requests.
- 2026-10-14 — Setup deviations from gui-3 (flagged): row "×" remove with confirm; member dots show real status; flash-rate slider drawn to 10 Hz with the >3 Hz region hatched and unreachable; minimal "+ Add fixture" form (virtual or LIFX by IP); "not added" rows get an **Add** button.
- 2026-10-14 — A corrupt/invalid `fixtures.json` is moved to `fixtures.json.bad-<time>` and the default rig is used (logged), instead of chromad failing to start.
- 2026-10-14 — Setup "Test" on real bulbs: acked `GetPower` + acked waveform (3 tries, 1 s each), "switched off" / "could not reach X at IP (no reply)" errors, a success toast that says "(fake bulb …)" on a fake engine; IP-only fixtures keep the fire-and-forget pulse (lifxlan matches acks by MAC). Root cause of the "Test did nothing" report: the debug app talks to the fake engine by default.
- 2026-10-15 — Pickup state describes the physical fader: with no board position seen yet everything counts as engaged (no ghost markers without a board); the first board report far from the stored value is ignored (no jump on connect); bank/fn switches disengage, then re-engage faders that already match; UI, cue, palette and encoder changes apply at once and disengage a board fader that no longer matches.
- 2026-10-15 — D2 seed: seeded channel values are display/pickup only. Until a channel moves, its fixtures keep the art colour/brightness, and only channels moved since the seed take part in HTP/LTP. Cue-recall colour ties go to the brighter channel.
- 2026-10-15 — D7 budget for caps below 3 Hz: `floor(2·hz)` edges per `n/(2·hz)` s (min 1 edge); identical values are never resent; a held value is released by the scheduler timer.
- 2026-10-15 — Output scheduler: LIFX ≥ 50 ms apart per bulb, virtual 60 Hz, newest value wins; latency p50/p95/max per class logged at shutdown ("console output latency").
- 2026-10-16 — `who` flips `Engine.enabled` (and seeds BOARD) inline, so intents after it on a connection see the new mode; config save and repaint stay in the background, FIFO. The console never pushes while the engine paints (`_push` checks).
- 2026-10-16 — A HUE fader at the top is 359.9° (no wrap to 0). Knob: 4 px drag or 40 px wheel delta = one detent; knobs send nothing without a touched channel. Palette swatch "selected" = within 8° hue / 0.12 sat of the last-touched colour.
- 2026-10-16 — USER DECISION (was open): BLACKOUT ON is never held by the D7 limiter; the brightness-0 value goes out at once and still counts as an edge, so coming back up (BLACKOUT off) waits for the budget. Worst case with blackout toggling: 7 edges in a sliding second (≈3.5 Hz) instead of 6. Only blackout bypasses; a fader pulled to 0 is still limited.

