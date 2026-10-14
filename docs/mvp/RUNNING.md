# Running the CHROMA Console app

Everything below runs from a checkout of this repo (normally `/Users/andy/Code/Chroma` on `main`).
The app never touches your bulbs, the Apple TV or the live hub's files unless you explicitly point it at them.

## One-time setup

```bash
cd /Users/andy/Code/Chroma
npm --prefix app install                      # UI + Tauri CLI (creates app/node_modules, git-ignored)
# hub/.venv already exists (the live hub's venv); chromad needs nothing new.
```

## 1. The app, with fake devices (safe, the default)

```bash
cd /Users/andy/Code/Chroma/app
npm run tauri dev
```

- First run compiles the Rust shell (a few minutes); later runs start in seconds.
- Opens the **CHROMA** window and a tray icon (menu bar). The app starts its own engine
  (`hub/chromad.py --fake`) in `~/.chroma/dev`: a fake Apple TV playing "Older", "Levitating", "Midnight City"
  and five fake bulbs. The top-right corner says **fake devices**.
- Close the window: the engine keeps running (tray → **Open CHROMA** brings it back).
- Tray → **Quit CHROMA** (or ⌘Q) stops the app and the engine it started.
- Engine log: `~/.chroma/dev/chromad.log`.
- **Setup** tab: the fake rig (5 Living Room bulbs, 3 "not added" bedroom bulbs, planned WLED, a virtual light).
  Discover, Test, add/remove fixtures, group safety limits and the bank grid all work against the fake engine.

## 2. The UI in a normal browser (mock engine, no Rust, no Python)

```bash
cd /Users/andy/Code/Chroma/app
npm run dev:mock                         # then open http://localhost:1420/
```

- `http://localhost:1420/?mock=mockup-console` shows the exact data of `docs/mvp/gui-1-console.png`.
- `?mock=live` (default) plays and responds to clicks; `?mock=empty` is "nothing playing".

## 3. Watch / poke the engine from a terminal

```bash
CHROMA_HOME=~/.chroma/dev python3 tools/chromactl.py watch      # live state + patches
CHROMA_HOME=~/.chroma/dev python3 tools/chromactl.py state      # full state
CHROMA_HOME=~/.chroma/dev python3 tools/chromactl.py op set_art_mode mode=paint
```

## 4. The app against the real Apple TV, without the bulbs

```bash
mkdir -p /tmp/chroma-hw && cp ~/.chroma/atv_credentials.json /tmp/chroma-hw/
cd /Users/andy/Code/Chroma/app
CHROMA_HOME=/tmp/chroma-hw CHROMAD_ARGS="--no-lifx" npm run tauri dev
```

Separate home + `--no-lifx`: real now-playing / artwork / position, no bulbs, the live hub untouched.

## Knobs (environment)

| Variable | Default (dev) | Meaning |
|---|---|---|
| `CHROMA_HOME` | `~/.chroma/dev` | engine state dir: socket, artwork, logs |
| `CHROMAD_ARGS` | `--fake` | extra chromad args (`""` = real devices; `--no-lifx` = no bulbs) |
| `CHROMAD_CMD` | `hub/.venv/bin/python` + `hub/chromad.py` | the engine command |
| `CHROMA_SUPERVISE` | `1` | `0` = only attach to an already-running chromad |
| `CHROMA_DEBUG_IPC` | unset | log every UI request in the terminal |

A packaged `.app` (double-click to open, engine at login) arrives in M7.

## Tests

```bash
tools/test_all.sh
```
