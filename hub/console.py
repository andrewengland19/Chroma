# =============================================================================
# hub/console.py — the console model (BUILD_ORDER §5.2–5.3). Pure logic, no I/O.
#
# M1: the data model and defaults that the State needs. M4 adds intents,
# arbitration (HTP/LTP, master, blackout, flash, pickup), cues and palette.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

BANKS = ("A", "B", "C")
CHANNELS_PER_BANK = 8

# gui-1 palette swatches and cue names (editable; persisted in console.json from M4).
DEFAULT_PALETTE = ["#e5484d", "#f5a05b", "#f2c94c", "#4ade80", "#2dd4bf", "#3b82f6", "#a855f7", "#f472b6"]
DEFAULT_CUE_NAMES = ["Intro", "Build", "DROP", "Chill", "Chorus", "Break", "Outro", "Blackout"]


@dataclass
class Channel:
    bank: str
    ch: int
    group_id: Optional[str] = None
    level: float = 0.0
    hue: float = 0.0
    sat: float = 0.0
    flash: bool = False
    engaged: bool = True

    def to_state(self) -> dict:
        return {"bank": self.bank, "ch": self.ch, "group_id": self.group_id,
                "level": round(self.level, 4), "hue": round(self.hue % 360.0, 2),
                "sat": round(self.sat, 4), "flash": self.flash, "engaged": self.engaged}


@dataclass
class Cue:
    name: str
    look: Optional[list] = None      # 24 x {level, hue, sat} or None (empty)
    fade_ms: int = 0


@dataclass
class ConsoleState:
    bank: str = "A"
    fader_fn: str = "level"
    master: float = 1.0
    blackout: bool = False
    channels: list = field(default_factory=list)
    last_touched: Optional[tuple] = None      # (bank, ch)
    palette: list = field(default_factory=lambda: list(DEFAULT_PALETTE))
    cues: list = field(default_factory=lambda: [Cue(n) for n in DEFAULT_CUE_NAMES])
    cue_active: Optional[int] = None

    @classmethod
    def default(cls, banks: Optional[dict] = None) -> "ConsoleState":
        """24 channels; `banks` maps "A"/"B"/"C" -> 8 group ids (or None)."""
        banks = banks or {}
        chans = [Channel(b, i + 1, (banks.get(b) or [None] * 8)[i])
                 for b in BANKS for i in range(CHANNELS_PER_BANK)]
        return cls(channels=chans)

    def channel(self, bank: str, ch: int) -> Channel:
        return self.channels[BANKS.index(bank) * CHANNELS_PER_BANK + (ch - 1)]

    def to_state_fields(self) -> dict:
        lt = self.last_touched
        return {
            "bank": self.bank,
            "fader_fn": self.fader_fn,
            "master": round(self.master, 4),
            "blackout": self.blackout,
            "channels": [c.to_state() for c in self.channels],
            "last_touched": {"bank": lt[0], "ch": lt[1]} if lt else None,
            "cues": [{"id": i, "name": c.name, "stored": c.look is not None} for i, c in enumerate(self.cues)],
            "cue_active": self.cue_active,
            "palette": list(self.palette),
        }
