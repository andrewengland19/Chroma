# Connecting Chroma to Home Assistant

Status: **stub**. `hub/homeassistant.py` (REST + WebSocket client) and `hub/outputs/ha.py` (light output) are
written and tested against a fake HA, and are **off by default**. Nothing has talked to a real Home Assistant
yet (HARDWARE_CHECKLIST HA.1). Connecting is config, not code, apart from the two-line wiring in §5.

## 1. In Home Assistant (once it is running)

1. Profile (your name, bottom-left) → **Security** → **Long-lived access tokens** → **Create token**, name it
   `chroma`. Copy it now; HA shows it only once.
2. Note the URL you reach HA at, e.g. `http://homeassistant.local:8123` or `http://<server-ip>:8123`.
   Use the plain LAN address (Chroma talks http/ws on your LAN; there is no auth between Chroma and you).

## 2. Store the token (Keychain, never config or git)

```bash
security add-generic-password -U -s chroma-hub -a homeassistant -w 'PASTE_TOKEN_HERE'
```

(Or `CHROMA_HA_TOKEN=...` in the environment for one-off runs.) Quote the token; prefix the command with a space
if your shell history would otherwise keep it.

## 3. Edit `~/.chroma/config.json`

Add an `ha` block (stop chromad first, or the next config save from the app can overwrite your edit):

```json
"ha": {
  "enabled": true,
  "url": "http://homeassistant.local:8123",
  "default_transition_ms": 1000,
  "max_brightness": 1.0,
  "entities": {
    "light.living_room_lamp": { "fixture": "lamp", "transition_ms": 800 },
    "light.hallway": "hallway"
  }
}
```

- `entities` maps an HA `light.*` entity to a **Chroma fixture id** (the slug shown in Setup). A bare string is
  shorthand for `{"fixture": "<id>"}`. `transition_ms` is per entity; omit it to use `default_transition_ms`.
- `max_brightness` (0–1) caps every command, including a bare turn_on. `min_interval_ms` can only be raised:
  the floor is 340 ms per entity (3 Hz, BUILD_ORDER §8.1).
- A bad value (non-`light.*` entity, bad URL, out-of-range number) makes `chromactl ha` report the error and the connector stays off; unknown keys are ignored.

## 4. Check it

```bash
python3 tools/chromactl.py ha status    # {"ok": true, "state": "ok", "version": ...}
python3 tools/chromactl.py ha lights    # every light.* entity, with its Chroma mapping
```

`state` is one of `disabled`, `no_token`, `auth_failed`, `unreachable`, `ok`. These commands read config and the
Keychain directly and send no light commands; they work with chromad stopped.

## 5. Wiring (the one code step, deferred to avoid merge conflicts with M6/M7)

`daemon._push` currently dispatches on `f.type` (`lifx`, `virtual`). To drive HA lights: allow a fixture type
`ha` (fixtures.py validation, the contracts enum and the generated UI types), build
`HaOutput(HaClient(HaConfig.from_dict(cfg.ha)))` in the daemon, call `ha_out.push(f, hsb, fade_ms)` from `_push`,
and `await ha_out.flush()` once per drive tick. `push` only records the newest value per entity, `flush` sends at
most one command per entity per 340 ms. Until then HA is reachable for status/listing only.

## Safety behaviour

Only `light.*` entities are ever commanded. An error (bad token, HA down, 4xx/5xx) drops the frame: no retry, no
"recovery" turn_on, never a brighter value. Brightness 0 is sent as `turn_off`. The WebSocket reconnects with
1, 2, 4 … 30 s backoff and stops for good on a rejected token (check `chromactl ha status`). The token is never
logged, put in an error message, or written to config.
