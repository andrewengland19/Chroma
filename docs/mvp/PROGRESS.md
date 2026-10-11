# CHROMA Console MVP — progress

Durable memory for the build. On restart: re-read `BUILD_ORDER.md`, this file and `DECISIONS.md`, then continue
at the first milestone that is not **done**. AC status is one of: **pass** (observed), **pending-hardware**
(needs the user's devices; see `HARDWARE_CHECKLIST.md`), **fail**.

Plan: `/Users/andy/.claude/plans/pasted-content-id-0494-you-are-snoopy-whale.md` (approved 2026-10-11).
Worktree: `/Users/andy/Code/Chroma-app` (venv `hub/.venv`). Merges go into `/Users/andy/Code/Chroma` main with
`--no-ff`. Never push.

| Milestone | Branch | Status |
|---|---|---|
| M0 contracts, scaffolding, fakes | `app/m0-contracts` | in progress |
| M1 chromad, IPC, SongClock | `app/m1-chromad` | — |
| M2 Tauri shell + Console header | `app/m2-shell` | — |
| M3 fixtures, groups, banks, Setup | `app/m3-fixtures` | — |
| M4 console, intents, AUTO/BOARD | `app/m4-console` | — |
| M5 recorder, replay, Takes | `app/m5-takes` | — |
| M6 board link + simulator | `app/m6-board` | — |
| M7 packaging + cutover | `app/m7-cutover` | — |

---

## M0 — contracts, scaffolding, fakes

Started 2026-10-11.
