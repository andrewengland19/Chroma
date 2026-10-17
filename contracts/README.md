# CHROMA contracts

JSON Schema (2020-12) for every message and file in BUILD_ORDER §5. **These are the source of truth**: change
the schema first, then the code. `hub/tests/test_contracts.py` validates every example (`ok-*` must pass,
`bad-*` must fail); the UI's TypeScript types are generated from these files (`npm --prefix app run gen:types`).

| Schema | What |
|---|---|
| `common.schema.json` | shared definitions (bank, channel, unit, hex, ids) |
| `intent.schema.json` | §5.2 intents (UI, board, replay, CLI) |
| `state.schema.json` | §5.1 State (merge patches apply to it) |
| `ipc-request.schema.json`, `ipc-response.schema.json`, `ipc-event.schema.json` | §5.1 Unix-socket NDJSON |
| `take-header.schema.json`, `take-event.schema.json` | §5.4 take files (`~/.chroma/takes/*.jsonl`) |
| `board-packet.schema.json`, `board-frame.schema.json`, `board-map.md` | §5.5 board UDP protocol |
| `fixtures.schema.json` | `~/.chroma/fixtures.json` |
| `console.schema.json` | `~/.chroma/console.json` |
| `room.schema.json` | Scene Agent room model, `~/.chroma/scene-agent/room.yaml` (loader: `hub/room_model.py`) |
| `scene-spec.schema.json` | Scene Agent SceneSpec, the model's output (renderer: `hub/scene_render.py`) |

Approved deviations from BUILD_ORDER (resolved fields in take events, `boot` nonce, extra state fields, extra
ops) are listed in `docs/mvp/DECISIONS.md` (X1–X6).
