# Board protocol map (BUILD_ORDER §5.5)

Board ⇄ engine over one UDP port (default **9876**). The listener stays off until `board.pairing` has created
`~/.chroma/board.key` (32 random bytes). Schemas: [board-packet](board-packet.schema.json),
[board-frame](board-frame.schema.json).

## Button mask `b` (40 bits, hex `0x` + 10 digits)

Bit 0 is the least significant bit. A set bit = the button is held down right now (level-triggered).

| Bits | Buttons |
|---|---|
| 0–7 | FLASH 1–8 |
| 8–15 | ASSIGN 1–8 |
| 16–23 | PALETTE 1–8 |
| 24–31 | CUE 1–8 |
| 32–34 | BANK A, B, C |
| 35 | TAP (reserved — no intent in the MVP) |
| 36 | REC |
| 37 | PLAY |
| 38 | STOP |
| 39 | BLACKOUT |

Example: `0x00000000A5` = bits 0, 2, 5, 7 = FLASH 1, 3, 6 and 8 held.

## Other fields

- `f` — 9 values 0..1: faders 1–8, then MASTER. Faders are not motorised: the engine applies **pickup**.
- `e` — absolute encoder counts `[hue, sat, nudge]` (signed 32-bit, may wrap). The engine diffs counts:
  1 hue detent = 5°, 1 sat detent = 2 %, 1 nudge detent = 1 ms.
- `s` — toggle positions `[art mode, who, fader fn]`: art 0=DET 1=PAINT 2=AI; who 0=AUTO 1=BOARD;
  fn 0=LEVEL 1=HUE 2=SAT.
- `boot` — random per boot. `seq` increases per datagram within a boot.

## How the engine turns snapshots into intents

The engine **diffs consecutive accepted snapshots**, so a lost datagram is harmless (the next one carries the
full state). The first snapshot after the board comes online (or after a new `boot`) is a **baseline only**:
it produces no intents.

| Change | Intent / op |
|---|---|
| fader n moves (deadband 0.004) | `fader` ch=n (pickup applies) |
| master moves | `fader` ch="master" |
| FLASH n down / up | `flash` ch=n down=true / false |
| ASSIGN n pressed | `assign` ch=n (select only) |
| ASSIGN n held + hue encoder turns | `assign` ch=n group_id=<next/previous group> |
| PALETTE n released before 600 ms | `palette_apply` i=n-1 |
| PALETTE n held 600 ms | `palette_store` i=n-1 (no apply on release) |
| CUE n released before 600 ms | `cue_recall` i=n-1 |
| CUE n held 600 ms | `cue_store` i=n-1 |
| BANK A/B/C pressed | `bank` |
| BLACKOUT pressed | `blackout` on = not current |
| REC / PLAY / STOP pressed | same handlers as the `rec.*` / `play.*` IPC ops |
| hue / sat encoder turns | `hue` / `sat` delta |
| nudge encoder turns | `take.nudge` on the playing/selected take |
| toggles change | `set_art_mode` / `who` / `fader_fn` |

## Authentication

`mac` = first 8 bytes of HMAC-SHA256(key, M), lowercase hex, where **M is the exact datagram bytes with the
`mac` member removed**. To make that unambiguous the sender serialises `mac` as the **last** member, as
`,"mac":"<16 hex>"}`, with no whitespace inside that suffix. The receiver checks the datagram ends with that
pattern and computes the HMAC over `datagram[:start_of_suffix] + b"}"`.

```
datagram = {"v":1,"id":"board-1","boot":7,"seq":812,...,"s":[2,1,0],"mac":"a1b2c3d4e5f60718"}
M        = {"v":1,"id":"board-1","boot":7,"seq":812,...,"s":[2,1,0]}
```

Engine → board frames use the same rule.

## Acceptance rules

- Reject (silently, counted) a datagram that is > 512 bytes, not JSON, fails the schema, or has a bad MAC.
- Per `(id, boot)`: apply a snapshot only if its `seq` is greater than the newest applied one. Older snapshots
  are never applied (they would roll state back); those within 8 of the newest are dropped quietly, older
  ones are counted as replays.
- A `boot` value seen before for this board id (kept in `~/.chroma/board_boots.json`, last 64) is rejected,
  so old captures cannot be replayed after a reboot.
- **Offline** = no accepted datagram for 3 s: release every held flash, keep fader values, set
  `health.board = "offline"`. The next accepted datagram is a baseline.
