# Legacy code (archived 2026-10-18)

Superseded by the Tauri app (`app/`) and the `chromad` engine (`hub/`). Kept for reference only; nothing here is
run by the current stack.

| Path | What it was |
|---|---|
| `chroma/` | Electron menu-bar app (React renderer worth porting from, sidecar in `chroma/python/`) |
| `lifx_music_app.py` | Original rumps/tkinter album-art painter; its pure colour logic lives on in `hub/color_pipeline.py` |
| `com.andrewengland19.lifx-music.plist` | LaunchAgent template for the old daemon (never installed by the app) |

Still at the repo root on purpose: `lifx_music_daemon.py` is launched by path from `~/.local/bin/lite` (`lite chroma`).
It is **deprecated**; retire it together with the `lite chroma` subcommand in M7. Nothing was deleted: moving files
back is a `git mv`. Deleting this folder needs the user's approval (BUILD_ORDER §10).
