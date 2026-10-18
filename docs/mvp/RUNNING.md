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

See "Packaged app" below for the double-clickable `.app` (M7).

## Devices: Fake | Real (Chroma → Devices, or the tray)

Switches the engine at once, no relaunch: the app stops the engine it started (an engine started elsewhere keeps
running), then starts or attaches to the other one; the window reloads its state from it.

- **Fake**: FakeProvider + fake bulbs, home `~/.chroma/dev` (`--fake`). Nothing real is touched.
- **Real**: the Apple TV and the LIFX bulbs, home `~/.chroma` (the live hub's home: credentials, layout). If
  `lite show` is running it is stopped first (a real chromad refuses to drive the bulbs next to it; toast says so).

A home given at launch is kept for the mode it was launched in (e.g. `CHROMA_HOME=/tmp/chroma-hw CHROMAD_ARGS=""`
makes Real use `/tmp/chroma-hw`); other `CHROMAD_ARGS` (`--no-lifx`, `--log-level …`) carry over. The choice is saved
(`~/Library/Application Support/com.chroma.console/devices.json`) and used at the next launch, including Force
Update relaunches, **unless `CHROMA_HOME` or `CHROMAD_ARGS` is set** (an explicit environment wins). So after picking
Real, a plain `npm run tauri dev` starts on real devices; pick Fake to go back.
Starting in a saved Real choice also stops `lite show` first.

Caveat (M4 design): AUTO/BOARD is `enabled` in the home's `config.json`. Real shares `~/.chroma` with `lite show`, so
if you leave the app in BOARD (or REC/PLAY switched it), a later `lite show start` comes up with painting off. Click
AUTO before switching back to Fake (or switch to Real again and click AUTO).

## Agent tab

Describe the room in plain words ("rainy evening, cozy but I need to read"); the lights agent
(`hub/lights_agent.py`, the same one behind the hub's `/agent` and ⌃⇧L) sets the look right away. It needs
**Devices → Real** (it addresses the real bulbs from `~/.lifx_cli_cache.json` whatever the engine mode, so a fake
engine refuses it) and an Anthropic key (`lite show setkey`). Asking switches WHO DRIVES to BOARD so album art won't
paint over the look; click AUTO on the Console to hand the lights back. **Undo** restores the room as it was before
the last run (shared with ⌃⇧L / `/agent`: a run there replaces the undo snapshot). **Save scene** stores the current
room as the new lite scene `agent.<name>` (create-only, never overwrites); recall it with `lite scene agent.<name>`.
In `dev:mock` the agent is simulated.

## Force Update (tray menu, or Chroma → Force Update)

Quits the app, stops `lite show` if it runs, ends every CHROMA process (all chromad engines whatever their home, app
binaries, `tauri dev` / Vite of any Chroma checkout, the old Electron app; SIGTERM first so engines save, SIGKILL
after 5 s), then relaunches the latest dev version: `npm run tauri dev` in the main checkout
(`/Users/andy/Code/Chroma`, found from the git worktree; `CHROMA_DEV_CHECKOUT` overrides), running `npm install`
first if `package-lock.json` changed. "Latest" = whatever local `main` (or the checked-out branch) has: nothing is
pulled, no branch is switched. `CHROMA_HOME` / `CHROMAD_ARGS` / `CHROMAD_CMD` / `CHROMA_SUPERVISE` carry over. The
relaunched app runs detached (no terminal); its output and the steps go to `~/Library/Logs/CHROMA/force-update.log`.
Plan only, kills nothing: `FORCE_UPDATE_DRY_RUN=1 bash app/src-tauri/force_update.sh 0 /Users/andy/Code/Chroma`.

## Packaged app (M7)

A clean checkout builds a self-contained `Chroma.app` (no Python or Node needed to *run* it). Needs: Rust,
Node, Python 3.13 with `hub/.venv` (below). Apple-silicon Mac (the sidecar is built for the host's architecture).

```bash
cd /Users/andy/Code/Chroma
python3 -m venv hub/.venv && hub/.venv/bin/pip install -r hub/requirements.txt   # once
npm --prefix app install                                                         # once
cd app && npm run bundle
open src-tauri/target/release/bundle/macos/Chroma.app
```

- `npm run bundle` = `tools/build_sidecar.sh` (PyInstaller freezes `hub/chromad.py` into
  `app/src-tauri/sidecar/chromad/`, ~30 s, installs PyInstaller into `hub/.venv` if missing) + `tauri build --bundles app`
  with `src-tauri/tauri.sidecar.conf.json`, which puts that folder in the app as `Contents/Resources/chromad/`.
  `tools/build_sidecar.sh --smoke` builds the engine alone, starts it with `--fake` and checks that it answers.
- A release app defaults to the **real** engine in `~/.chroma` (the bundled `chromad`); Devices → Fake still works.
  `CHROMA_HOME` / `CHROMAD_ARGS` / `CHROMAD_CMD` / `CHROMA_SUPERVISE` behave as above.
- Plain `npm run tauri build` (no `--config`) makes an app without the engine; it then needs `CHROMAD_CMD`.
- Re-run `npm run bundle` after any `hub/` change; `tauri dev` is unaffected (it runs `hub/.venv` + `chromad.py`).
- **Install and Dock icon**: `tools/install_app.sh` (builds if needed, or `tools/install_app.sh /path/Chroma.app`)
  copies the app to `/Applications/Chroma.app`, replacing only a previous Chroma (same bundle id) and refusing while
  Chroma is running. It does not touch the Dock: drag Chroma from `/Applications` onto the Dock, or launch it and
  right-click its Dock icon → Options → Keep in Dock.
- **Gatekeeper, once** (unsigned app that came from a download or another Mac; an app built on this Mac is not
  quarantined): right-click Chroma in `/Applications` → Open → Open, or
  `xattr -dr com.apple.quarantine /Applications/Chroma.app`.
- **Icon**: single master `app/src-tauri/icons/source/chroma-icon.svg` (prism and spectrum on a dark rounded square, 1024
  canvas, 824 px tile), plus `tray-template.svg` for the monochrome menu-bar icon. Regenerate with
  `rsvg-convert -w 1024 -h 1024 chroma-icon.svg -o /tmp/m.png && cd app && npx tauri icon /tmp/m.png`, then delete the
  `icons/android`, `icons/ios` and `64x64.png` it adds, and re-render the tray PNG
  (`rsvg-convert -w 36 -h 36 tray-template.svg -o ../tray-template.png`).
- Not done: code signing, hardened runtime, notarization, universal2 / Intel. An unsigned app opened from a download
  needs right-click → Open the first time; the Local Network prompt (Apple TV, bulbs) appears on first real use.
  Needed to ship it to others: an Apple Developer ID certificate, `codesign --options runtime` on the app and on every
  Mach-O in `Resources/chromad/` (PyInstaller's `--codesign-identity`), the `com.apple.security.network.client`-style
  entitlements for the runtime, then `notarytool submit` and `stapler staple`.

### Engine at login (LaunchAgent)

Tray (or **Chroma** menu) → **Run engine at login** (a check item) writes
`~/Library/LaunchAgents/com.chroma.chromad.plist`; unchecking removes it (and stops the engine launchd started).
Nothing is written before you click, and installing does not start anything now: it takes effect at the next login,
when chromad runs headless with no window (real devices, `~/.chroma`, log `~/.chroma/chromad.log`). Opening the app
then **attaches** to it (status "Engine: running"); Quit leaves it running. A crash restarts it; `lite show stop`
leaves it stopped until the next login. The plist stores the engine's path: a bundled app writes the sidecar inside the
`.app`, a dev build writes `hub/.venv/bin/python` + `hub/chromad.py` of the checkout. Click it off and on after moving
or rebuilding the app. By hand: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chroma.chromad.plist`
starts it now; `launchctl bootout gui/$(id -u)/com.chroma.chromad` stops it.

### `lite show` (the CLI)

`lite show start|stop|status|restart|refresh` controls **chromad** (the same engine the app and the LaunchAgent run):
`start` spawns it detached (log `~/.chroma/chromad.log`), `stop` signals the pid in `~/.chroma/chromad.pid`, `status`
prints pid / device / lights / track, `refresh` repaints now through the socket. `lite show setkey` is unchanged.
After this change, `lite show start` does **not** serve `http://<mac>:8765/` (Lights tab, TV Remote, `/agent`,
Ctrl+Shift+L): for those run `lite show start --legacy` instead (the old `server.py` hub; it and chromad refuse to run
together on one home because both drive the bulbs). `lite` itself is outside the repo: apply
`docs/mvp/lite-show.patch` (`cd ~ && patch -p1 < /Users/andy/Code/Chroma/docs/mvp/lite-show.patch`; it only passes
`--legacy` through and updates the help text). Without the patch `lite show …` already delegates to chromad because
`lite` runs `hub/showctl.py`.

## Tests

```bash
tools/test_all.sh
```
