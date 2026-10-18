# Legacy code (archived 2026-10-18)

Superseded by the Tauri app (`app/`) and the `chromad` engine (`hub/`). Kept for reference only; nothing here is
run by the current stack.

| Path | What it was |
|---|---|
| `chroma/` | Electron menu-bar app (React renderer worth porting from, sidecar in `chroma/python/`) |
| `lifx_music_app.py` | Original rumps/tkinter album-art painter; its pure colour logic lives on in `hub/color_pipeline.py` |
| `com.andrewengland19.lifx-music.plist` | LaunchAgent template for the old daemon (never installed by the app) |

Retired 2026-10-18 (deleted from the tree, recoverable from git tag `legacy-backup-2026-10-18`): `lifx_music_daemon.py`
and the hub Music page (`hub/web/index.html`, `app.js`). `lite chroma` now prints a pointer to `lite show start`. Nothing was deleted: moving files
back is a `git mv`. Deleting this folder needs the user's approval (BUILD_ORDER §10).
