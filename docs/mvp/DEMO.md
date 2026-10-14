# CHROMA Console MVP — demo commands

Exact commands that demonstrate each milestone's acceptance checks without hardware. Run from the repo root
with the hub venv (`hub/.venv`, created from `hub/requirements.txt` + `hub/requirements-dev.txt`).

## All tests

```bash
tools/test_all.sh
```

## M0 — contracts, scaffolding, fakes

```bash
# Engine + contract tests (HOME is sandboxed; no real devices are touched)
(cd hub && .venv/bin/python -m pytest -q)

# Only the contract examples
(cd hub && .venv/bin/python -m pytest -q tests/test_contracts.py)

# FakeProvider: scripted 3-minute track (position, pause/resume, seek, track change)
(cd hub && .venv/bin/python -m pytest -q tests/test_fake_provider.py)

# Empty app window
npm --prefix app install
(cd app && npm run tauri dev)
```

## M1 — chromad, IPC, SongClock

```bash
# chromad with no hardware, in an isolated home (keep socket paths short: macOS limit is 104 bytes)
H=$(mktemp -d -t cx)
hub/.venv/bin/python hub/chromad.py --fake --home $H &

# AC: watch streams state, then position patches (~4 Hz) and any change
CHROMA_HOME=$H python3 tools/chromactl.py watch --count 10
CHROMA_HOME=$H python3 tools/chromactl.py state
CHROMA_HOME=$H python3 tools/chromactl.py op set_art_mode mode=paint
CHROMA_HOME=$H python3 tools/chromactl.py intent who who=board
CHROMA_HOME=$H python3 tools/chromactl.py op refresh
kill %1

# Tests
(cd hub && .venv/bin/python -m pytest -q tests/test_song_clock.py tests/test_ipc.py tests/test_chromad.py)

# Position spike pipeline on fake data (real recording: HARDWARE_CHECKLIST H1.1)
hub/.venv/bin/python tools/position_spike.py record --fake --duration 4 --out $H/spike.jsonl
hub/.venv/bin/python tools/position_spike.py analyze $H/spike.jsonl
```

## M2 — Tauri shell + live Console header

See `docs/mvp/RUNNING.md` for every way to open the app.

```bash
# The real app with a fake engine (AC: track/art/progress live, ART MODE, WHO DRIVES)
(cd app && npm run tauri dev)

# Scripted lifecycle check (debug builds): close the window → engine keeps running;
# Quit → the engine the app started stops. Prints [selftest] lines.
(cd app && CHROMA_DEBUG_IPC=1 CHROMA_SELFTEST=close-quit npm run tauri dev)
pgrep -fl "chromad.py --home $HOME/.chroma/dev"      # empty after the self-test quits

# Mock UI in a browser + side-by-side with the mockup at 2x
(cd app && npm run dev:mock -- --port 1430) &
hub/.venv/bin/python tools/ui_compare.py console /tmp/ui --region 0 0 1178 310
```

## M3 — fixtures, groups, banks, outputs, Setup

```bash
# Engine: rig ops against a fake chromad
H=$(mktemp -d -t cx); hub/.venv/bin/python hub/chromad.py --fake --home $H &
CHROMA_HOME=$H python3 tools/chromactl.py op fixtures.list | head -40
CHROMA_HOME=$H python3 tools/chromactl.py op fixtures.discover
CHROMA_HOME=$H python3 tools/chromactl.py op fixtures.test fixture_id=couch-left
CHROMA_HOME=$H python3 tools/chromactl.py op groups.set_limits group_id=playbill max_level=0.5
CHROMA_HOME=$H python3 tools/chromactl.py op banks.set bank=C channel=1 group_id=fx
kill %1

# Tests
(cd hub && .venv/bin/python -m pytest -q tests/test_fixtures.py tests/test_outputs.py tests/test_rig_ops.py)
(cd app && npx vitest run src/screens/SetupScreen.test.tsx)

# The Setup tab in the app (fake devices): RUNNING.md §1, then click Setup
```
