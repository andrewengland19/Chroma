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
