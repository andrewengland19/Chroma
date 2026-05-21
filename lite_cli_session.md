# `lite` CLI — Session Export

**Date:** 2026-05-17
**Working directory:** `/Users/andy/Code/Chroma`
**User:** andrewengland19@gmail.com

---

## Request

> Create a new python script that runs in CLI and can be added to PATH to rapid-fire change light settings from CLI. Example usage:
> - `{commandname} couch-left green`
> - `{commandname} washer off`
> - `{commandname} living-room on .60`
>
> Ideally, runnable from any environment and any directory.

## Environment discovered

- Project: `/Users/andy/Code/Chroma` — LIFX-based lighting work (`lifx_music_app.py`, `lifx_music_daemon.py`).
- Python: `lifxlan` already installed at `/Users/andy/miniforge3/lib/python3.13/site-packages/lifxlan/`.
- `~/.local/bin` already on `PATH`.
- `lifxlan.Light(mac_addr, ip_addr)` — direct instantiation works, so discovery can be cached and skipped on the hot path.
- Existing daemon patterns: `LifxLAN().get_lights()`, `l.get_label()`, `l.get_group()`, `l.set_color(hsbk, duration, rapid=True)`.

## Design decisions

| Decision | Why |
| --- | --- |
| Command name `lite` | Short, easy to type, unambiguous. |
| Install path `/Users/andy/.local/bin/lite` | Already on `PATH`; no shell config needed. |
| Shebang `#!/Users/andy/miniforge3/bin/python3` | Hard-pinned so `lifxlan` resolves regardless of which shell/env invokes it. Satisfies "any environment". |
| Cache at `~/.lifx_cli_cache.json` | Avoids 3s LAN discovery on every call. Stores `{label, group, mac, ip}` per light. |
| Auto-rediscover on cache miss | If target isn't in cache, rediscover once and retry — handles new bulbs without manual `refresh`. |
| Transition 250 ms | Snappy for CLI feel (daemon uses 1500 ms for cinematic fades). |
| Matching is lenient | Case-insensitive; hyphens/underscores → spaces; unique substring matches work. Groups and lights share the `<target>` slot. |
| `rapid=True` on all writes | Fire-and-forget — no ack wait. |

## Final usage

```
lite <target> <action> [brightness]
lite refresh           # rediscover lights, rewrite cache
lite list              # show cached lights and groups
```

**Action** is one of:
- color name (`red`, `green`, `blue`, `cyan`, `warm`, `cool`, `white`, …)
- hex `#rrggbb`
- `on` / `off`
- a number — brightness only (0.0–1.0, or 0–100)

**Brightness** as a 3rd arg overrides the color's brightness:
`lite living-room blue .4`

## Color palette built in

- **Hues:** red, orange, amber, yellow, lime, green, mint, teal, cyan, sky, blue, indigo, purple, violet, magenta, pink, hotpink, rose
- **Whites (kelvin):** white (3500K), warm (2700K), candle (2000K), neutral (3500K), daylight (5000K), cool (6500K)

## File created

- `/Users/andy/.local/bin/lite` — the script (executable, `chmod +x`).

## Verification

```
$ which lite
/Users/andy/.local/bin/lite

$ lite --help
lite — rapid-fire LIFX control from the CLI.

Usage:
  lite <target> <action> [brightness]
  …
```

## Maintenance notes

- If miniforge moves, update the shebang on line 1 of `/Users/andy/.local/bin/lite`.
- Cache is regenerated automatically on miss, or manually with `lite refresh`.
- To add more colors, edit the `COLORS` dict (HSV in 0–1 space) or `WHITES` dict (kelvin, value).
