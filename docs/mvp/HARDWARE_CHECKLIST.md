# CHROMA Console MVP — hardware checklist

Checks that need the user's real devices (Apple TV, LIFX bulbs, the board, the live hub). The builder never runs
these autonomously. Each item lists exact steps; record the result and date in the **Result** column.

Status: **pending-hardware** until the user runs it.

| # | Milestone | Check | Steps | Result |
|---|---|---|---|---|
| H1.1 | M1 | Apple TV position spike (>= 5 min) | From `/Users/andy/Code/Chroma` (after the M1 merge): `hub/.venv/bin/python tools/position_spike.py record --duration 360`. While it runs, on the Apple TV: play a song ~1 min, pause ~15 s, resume, seek forward ~1 min, seek back ~30 s, skip to the next track, let it play ~1 min. Then `hub/.venv/bin/python tools/position_spike.py analyze ~/.chroma/spike/position-*.jsonl --md docs/mvp/position-spike.md` and send me the file (or tell me it's there). Read-only: no playback control, no bulbs. | pending-hardware |
| H1.2 | M1 | chromad's SongClock follows the real Apple TV | `mkdir -p /tmp/chroma-hw && cp ~/.chroma/atv_credentials.json /tmp/chroma-hw/` then `hub/.venv/bin/python hub/chromad.py --home /tmp/chroma-hw --no-lifx` in one terminal and `CHROMA_HOME=/tmp/chroma-hw python3 tools/chromactl.py watch` in another. Play / pause / seek on the Apple TV: `pos_ms` in the patches should follow within ~1 s and freeze on pause. Ctrl+C both. Separate home + `--no-lifx`: the live hub and the bulbs are untouched. | pending-hardware |
| H1.3 | M1 | `lite show` still works on the new main | `lite show restart`, then `lite show status` and `lite show refresh`. Output should look exactly as before. (Restarting picks up the merged code.) | pending-hardware |
