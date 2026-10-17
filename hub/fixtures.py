# =============================================================================
# hub/fixtures.py — fixtures, groups and the 3x8 bank map (BUILD_ORDER §3, M3).
#
# Persisted to $CHROMA_HOME/fixtures.json (contracts/fixtures.schema.json); `rev`
# bumps on every change and is stamped into takes as fixtures_rev.
#
#   fixture   one light: LIFX bulb, virtual (in-app) light, or a planned WLED node
#             status: active (driven) | not_added (discovered, not in the rig) | planned
#   group     what a fader controls: members + safety limits (max level, max flash
#             rate <= 3 Hz, idle auto-dim)
#   banks     A/B/C x 8 channels -> group id (or empty)
#   space     a room of the house (Living Room, Bedroom, ...): each fixture lives in
#             one; the Room panel shows one Space and the agent is scoped to Spaces.
#             living-room is the default and can't be removed. Seeded from the bulbs'
#             LIFX groups.
#
# A fixture in several groups gets the MOST restrictive limits of all its groups
# (DECISIONS D8). Validation is by hand (no jsonschema at runtime); the tests check
# every file this module writes against the schema.
# =============================================================================

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

BANKS = ("A", "B", "C")
MAX_FLASH_HZ = 3.0            # BUILD_ORDER §8.1 — never above, via any path
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
IP = re.compile(r"^[0-9]{1,3}(\.[0-9]{1,3}){3}$")
MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
LIVING_ROOM = "Living Room"
DEFAULT_SPACE = "living-room"


class RigError(ValueError):
    """A rejected fixture/group/bank edit (shown to the user)."""


def slugify(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")[:48].strip("-")
    return s or "fixture"


@dataclass
class Fixture:
    id: str
    label: str
    type: str                       # lifx | virtual | wled
    status: str                     # active | not_added | planned
    klass: str                      # slow | fast   (JSON key: "class")
    x: float = 0.5
    y: float = 0.5
    ip: Optional[str] = None
    mac: Optional[str] = None
    lifx_group: Optional[str] = None
    series: Optional[str] = None
    color: Optional[str] = None
    space: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "label": self.label, "type": self.type, "status": self.status,
             "class": self.klass, "x": round(self.x, 4), "y": round(self.y, 4)}
        for k in ("ip", "mac", "lifx_group", "series", "color", "space"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Fixture":
        if not isinstance(d, dict):
            raise RigError("fixture must be an object")
        kw = {k: d.get(k) for k in ("id", "label", "type", "status", "x", "y", "ip", "mac",
                                    "lifx_group", "series", "color", "space")}
        kw["klass"] = d.get("class", d.get("klass"))
        if kw["x"] is None:
            kw["x"] = 0.5
        if kw["y"] is None:
            kw["y"] = 0.5
        f = cls(**kw)
        f.validate()
        return f

    def validate(self) -> None:
        if not isinstance(self.id, str) or not SLUG.match(self.id):
            raise RigError(f"fixture id {self.id!r} must be lowercase letters, digits and dashes")
        if not isinstance(self.label, str) or not 1 <= len(self.label.strip()) <= 48:
            raise RigError("fixture name must be 1–48 characters")
        if self.type not in ("lifx", "virtual", "wled"):
            raise RigError(f"fixture type must be lifx, virtual or wled (got {self.type!r})")
        if self.status not in ("active", "not_added", "planned"):
            raise RigError(f"fixture status must be active, not_added or planned (got {self.status!r})")
        if self.klass not in ("slow", "fast"):
            raise RigError(f"fixture class must be slow or fast (got {self.klass!r})")
        for name in ("x", "y"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1:
                raise RigError(f"fixture {name} must be between 0 and 1")
        if self.ip is not None and not IP.match(self.ip):
            raise RigError(f"bad IP address {self.ip!r}")
        if self.mac is not None:
            self.mac = self.mac.lower()
            if not MAC.match(self.mac):
                raise RigError(f"bad MAC address {self.mac!r}")
        if self.color is not None and not HEX.match(self.color):
            raise RigError(f"bad colour {self.color!r}")
        if self.space is not None and (not isinstance(self.space, str) or not SLUG.match(self.space)):
            raise RigError(f"bad space id {self.space!r}")
        if self.type == "lifx" and self.status == "active" and not self.ip:
            raise RigError("an active LIFX fixture needs its IP address (or use Discover LIFX)")
        if self.type == "wled" and self.status == "active":
            raise RigError("WLED output arrives in phase 2 — add it as planned")


@dataclass
class Group:
    id: str
    name: str
    color: str
    members: list = field(default_factory=list)
    max_level: float = 1.0
    max_flash_hz: float = MAX_FLASH_HZ
    idle_dim_min: Optional[int] = None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "color": self.color, "members": list(self.members),
                "max_level": round(self.max_level, 4), "max_flash_hz": self.max_flash_hz,
                "idle_dim_min": self.idle_dim_min}

    @classmethod
    def from_dict(cls, d: dict) -> "Group":
        if not isinstance(d, dict):
            raise RigError("group must be an object")
        g = cls(id=d.get("id"), name=d.get("name"), color=d.get("color"),
                members=list(d.get("members") or []),
                max_level=d.get("max_level", 1.0), max_flash_hz=d.get("max_flash_hz", MAX_FLASH_HZ),
                idle_dim_min=d.get("idle_dim_min"))
        g.validate()
        return g

    def validate(self) -> None:
        if not isinstance(self.id, str) or not SLUG.match(self.id):
            raise RigError(f"group id {self.id!r} must be lowercase letters, digits and dashes")
        if not isinstance(self.name, str) or not 1 <= len(self.name.strip()) <= 16:
            raise RigError("group name must be 1–16 characters (it is the fader's scribble strip)")
        if not isinstance(self.color, str) or not HEX.match(self.color):
            raise RigError(f"bad group colour {self.color!r}")
        if not isinstance(self.max_level, (int, float)) or isinstance(self.max_level, bool) \
                or not 0 <= self.max_level <= 1:
            raise RigError("max level must be between 0 and 100 %")
        hz = self.max_flash_hz
        if not isinstance(hz, (int, float)) or isinstance(hz, bool) or not 0 < hz <= MAX_FLASH_HZ:
            raise RigError(f"max flash rate must be above 0 and at most {MAX_FLASH_HZ:g} Hz")
        if self.idle_dim_min is not None and (not isinstance(self.idle_dim_min, int)
                                              or isinstance(self.idle_dim_min, bool)
                                              or not 1 <= self.idle_dim_min <= 240):
            raise RigError("idle auto-dim must be off or 1–240 minutes")
        if len(set(self.members)) != len(self.members):
            raise RigError("a fixture is listed twice in the group")


@dataclass
class Space:
    id: str
    name: str

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict) -> "Space":
        if not isinstance(d, dict):
            raise RigError("space must be an object")
        sp = cls(id=d.get("id"), name=d.get("name"))
        if not isinstance(sp.id, str) or not SLUG.match(sp.id):
            raise RigError(f"space id {sp.id!r} must be lowercase letters, digits and dashes")
        if not isinstance(sp.name, str) or not 1 <= len(sp.name.strip()) <= 24:
            raise RigError("space name must be 1–24 characters")
        sp.name = sp.name.strip()
        return sp


# ---- the default rig (gui-3 mockup, mapped onto the real bulbs — DECISIONS D5) -----------

GROUP_COLORS = {"red": "#e5484d", "orange": "#f59e5b", "yellow": "#f2c94c", "green": "#4ade80",
                "teal": "#2dd4bf", "blue": "#3b82f6", "purple": "#a855f7", "pink": "#f472b6"}

DEFAULT_GROUPS = [
    # id, name, colour, max level, idle dim
    ("lr-all", "LR ALL", "red", 1.0, None), ("bed", "BED", "orange", 1.0, None),
    ("counter", "COUNTER", "yellow", 1.0, None), ("playbill", "PLAYBILL", "green", 0.6, 20),
    ("lr-l", "LR L", "teal", 1.0, None), ("lr-r", "LR R", "blue", 1.0, None),
    ("accent", "ACCENT", "purple", 1.0, None), ("fx", "FX", "pink", 1.0, None),
    ("tv", "TV", "red", 1.0, None), ("couch", "COUCH", "orange", 1.0, None),
    ("lamp", "LAMP", "yellow", 1.0, None), ("pb-1-4", "PB 1–4", "green", 0.6, None),
    ("pb-5-8", "PB 5–8", "teal", 0.6, None), ("pb-9-12", "PB 9–12", "blue", 0.6, None),
    ("br-1-2", "BR 1–2", "purple", 1.0, None), ("br-3-4", "BR 3–4", "pink", 1.0, None),
]
DEFAULT_BANKS = {
    "A": ["lr-all", "bed", "counter", "playbill", "lr-l", "lr-r", "accent", "fx"],
    "B": ["tv", "couch", "lamp", "pb-1-4", "pb-5-8", "pb-9-12", "br-1-2", "br-3-4"],
    "C": [None] * 8,
}


def default_groups_for(label: str, lifx_group: Optional[str], living_room: str = LIVING_ROOM) -> list:
    """Group ids a newly found LIFX bulb joins, from its label and LIFX group."""
    lo = label.lower()
    if (lifx_group or "").lower() == living_room.lower():
        gs = ["lr-all"]
        if "left" in lo:
            gs.append("lr-l")
        elif "right" in lo:
            gs.append("lr-r")
        if "tv" in lo:
            gs.append("tv")
        elif "couch" in lo or "sofa" in lo:
            gs.append("couch")
        if not any(g in gs for g in ("lr-l", "lr-r", "tv", "couch")):
            gs += ["accent", "lamp"]           # the mockup's "Lamp" slot (D5: Washer)
        return gs
    if "bed" in (lifx_group or "").lower():
        return ["bed"]
    return []


# ---- the store ----------------------------------------------------------------------------

class FixtureStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.rev = 0
        self.fixtures: dict = {}      # id -> Fixture (insertion order = table order)
        self.groups: dict = {}        # id -> Group
        self.banks: dict = {b: [None] * 8 for b in BANKS}
        self.spaces: dict = {}        # id -> Space (insertion order = tab order)
        self.fresh = False            # True when seeded this run (first start): auto-import bulbs
        self.recovered: Optional[str] = None   # a corrupt fixtures.json was moved here

    # ---- persistence ---------------------------------------------------------
    @classmethod
    def load(cls, path: str) -> "FixtureStore":
        """Load fixtures.json; a missing file seeds the default rig. A corrupt or invalid
        file is kept aside as fixtures.json.bad-<time> and the default rig is used, so
        chromad never crash-loops on it (`recovered` names the kept file)."""
        try:
            return cls._load(path)
        except (ValueError, KeyError, TypeError, OSError) as e:
            import logging
            import time
            bad = f"{path}.bad-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                os.replace(path, bad)
            except OSError:
                bad = None
            logging.getLogger("chroma-hub").error(f"fixtures.json unreadable ({e}); kept as {bad}, using the default rig")
            s = cls(path).seed_default()
            s.recovered = bad
            return s

    @classmethod
    def _load(cls, path: str) -> "FixtureStore":
        s = cls(path)
        if not os.path.exists(path):
            return s.seed_default()
        with open(path) as f:
            d = json.load(f)
        s.rev = int(d.get("rev", 0))
        for fd in d.get("fixtures", []):
            fx = Fixture.from_dict(fd)
            s.fixtures[fx.id] = fx
        for gd in d.get("groups", []):
            g = Group.from_dict(gd)
            s.groups[g.id] = g
        banks = d.get("banks") or {}
        for b in BANKS:
            row = list(banks.get(b) or [None] * 8)[:8]
            s.banks[b] = row + [None] * (8 - len(row))
        for sd in d.get("spaces") or []:
            sp = Space.from_dict(sd)
            s.spaces[sp.id] = sp
        if s._ensure_spaces() or "spaces" not in d:
            s.save()                  # a file from before Spaces: seeded from the LIFX groups
        return s

    def to_dict(self) -> dict:
        return {"v": 1, "rev": self.rev, "fixtures": [f.to_dict() for f in self.ordered()],
                "groups": [g.to_dict() for g in self.groups.values()],
                "banks": {b: list(self.banks[b]) for b in BANKS},
                "spaces": [sp.to_dict() for sp in self.spaces.values()]}

    def save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def _changed(self) -> None:
        self.rev += 1
        self.save()

    def seed_default(self) -> "FixtureStore":
        """The mockup rig minus real bulbs (Discover / the first engine discovery adds them)."""
        for gid, name, color, max_level, idle in DEFAULT_GROUPS:
            self.groups[gid] = Group(gid, name, GROUP_COLORS[color], [], max_level, MAX_FLASH_HZ, idle)
        self._add(Fixture("counter-strip", "Counter strip", "wled", "planned", "fast", 0.92, 0.5))
        for i in range(1, 13):
            fx = Fixture(f"playbill-{i:02d}", f"playbill-{i:02d}", "wled", "planned", "fast",
                         round(0.1 + 0.8 * (i - 1) / 11, 3), 0.05, series="Playbill 01–12")
            self._add(fx)
            self.groups["playbill"].members.append(fx.id)
            self.groups[f"pb-{((i - 1) // 4) * 4 + 1}-{((i - 1) // 4) * 4 + 4}"].members.append(fx.id)
        self.groups["counter"].members.append("counter-strip")
        self._add(Fixture("virtual-light-1", "Virtual Light 1", "virtual", "active", "fast", 0.5, 0.88,
                          color="#3b82f6"))
        self.groups["fx"].members.append("virtual-light-1")
        self.banks = {b: list(row) for b, row in DEFAULT_BANKS.items()}
        self._ensure_spaces()
        self.rev = 1
        self.fresh = True
        self.save()
        return self

    def _add(self, fx: Fixture) -> None:
        self.fixtures[fx.id] = fx

    # ---- spaces ----------------------------------------------------------------------
    def space_for_group(self, lifx_group: Optional[str]) -> str:
        """The Space for a LIFX group (created on first sight); no group -> living-room."""
        name = (lifx_group or "").strip()[:24]
        sid = slugify(name) if name else DEFAULT_SPACE
        if sid not in self.spaces:
            self.spaces[sid] = Space(sid, name or LIVING_ROOM)
        return sid

    def _ensure_spaces(self) -> bool:
        """living-room exists and every fixture is in a known Space. True if anything changed."""
        changed = False
        if DEFAULT_SPACE not in self.spaces:
            self.spaces = {DEFAULT_SPACE: Space(DEFAULT_SPACE, LIVING_ROOM), **self.spaces}
            changed = True
        for f in self.fixtures.values():
            if f.space not in self.spaces:
                f.space = self.space_for_group(f.lifx_group) if f.type == "lifx" else DEFAULT_SPACE
                changed = True
        return changed

    def upsert_space(self, d: dict) -> Space:
        if not isinstance(d, dict):
            raise RigError("space must be an object")
        d = dict(d)
        if not d.get("id"):
            base, n = slugify(d.get("name", "")), 2
            if base == "fixture":
                base = "space"
            sid = base
            while sid in self.spaces:
                sid = f"{base[:44]}-{n}"
                n += 1
            d["id"] = sid
        sp = Space.from_dict(d)
        if any(o.name.lower() == sp.name.lower() and o.id != sp.id for o in self.spaces.values()):
            raise RigError(f"there is already a space called {sp.name}")
        self.spaces[sp.id] = sp
        self._changed()
        return sp

    def remove_space(self, sid: str) -> None:
        """Its lights move to living-room, which itself can't be removed."""
        if sid not in self.spaces:
            raise RigError(f"no space {sid!r}")
        if sid == DEFAULT_SPACE:
            raise RigError(f"{self.spaces[sid].name} is the default space — rename it instead")
        del self.spaces[sid]
        for f in self.fixtures.values():
            if f.space == sid:
                f.space = DEFAULT_SPACE
        self._changed()

    def in_spaces(self, sids) -> list:
        """Fixtures (any status) whose Space is one of `sids`."""
        want = set(sids)
        return [f for f in self.ordered() if f.space in want]

    def _unique_id(self, base: str) -> str:
        fid, n = base, 2
        while fid in self.fixtures:
            fid = f"{base[:44]}-{n}"
            n += 1
        return fid

    # ---- edits -------------------------------------------------------------------
    def upsert_fixture(self, d: dict) -> Fixture:
        if not isinstance(d, dict):
            raise RigError("fixture must be an object")
        d = dict(d)
        if not d.get("id"):
            d["id"] = self._unique_id(slugify(d.get("label", "")))
        fx = Fixture.from_dict(d)
        if fx.space is None:
            old = self.fixtures.get(fx.id)
            fx.space = old.space if old else DEFAULT_SPACE      # a UI from before Spaces
        if fx.space not in self.spaces:
            raise RigError(f"no space {fx.space!r}")
        if fx.mac:
            clash = [o for o in self.fixtures.values() if o.mac == fx.mac and o.id != fx.id]
            if clash:
                raise RigError(f"{clash[0].label} already uses MAC {fx.mac}")
        self.fixtures[fx.id] = fx
        self._changed()
        return fx

    def remove_fixture(self, fid: str) -> None:
        if fid not in self.fixtures:
            raise RigError(f"no fixture {fid!r}")
        del self.fixtures[fid]
        for g in self.groups.values():
            if fid in g.members:
                g.members.remove(fid)
        self._changed()

    def upsert_group(self, d: dict) -> Group:
        g = Group.from_dict(d)
        missing = [m for m in g.members if m not in self.fixtures]
        if missing:
            raise RigError(f"unknown fixture(s) in group: {', '.join(missing)}")
        self.groups[g.id] = g
        self._changed()
        return g

    def set_limits(self, gid: str, **limits) -> Group:
        """Change only safety limits (members untouched: no lost updates from a stale UI)."""
        g = self.groups.get(gid)
        if g is None:
            raise RigError(f"no group {gid!r}")
        d = g.to_dict()
        for k in ("max_level", "max_flash_hz", "idle_dim_min"):
            if k in limits:
                d[k] = limits[k]
        return self.upsert_group(d)

    def swap_banks(self, a: tuple, b: tuple) -> None:
        for bank, ch in (a, b):
            if bank not in BANKS or not isinstance(ch, int) or isinstance(ch, bool) or not 1 <= ch <= 8:
                raise RigError("cells are a bank A–C and a channel 1–8")
        (ba, ca), (bb, cb) = a, b
        if (ba, ca) == (bb, cb):
            return
        self.banks[ba][ca - 1], self.banks[bb][cb - 1] = self.banks[bb][cb - 1], self.banks[ba][ca - 1]
        self._changed()

    def remove_group(self, gid: str) -> None:
        if gid not in self.groups:
            raise RigError(f"no group {gid!r}")
        del self.groups[gid]
        for b in BANKS:
            self.banks[b] = [None if x == gid else x for x in self.banks[b]]
        self._changed()

    def set_bank(self, bank: str, ch: int, gid: Optional[str]) -> None:
        if bank not in BANKS:
            raise RigError(f"bank must be A, B or C (got {bank!r})")
        if not isinstance(ch, int) or isinstance(ch, bool) or not 1 <= ch <= 8:
            raise RigError("channel must be 1–8")
        if gid is not None and gid not in self.groups:
            raise RigError(f"no group {gid!r}")
        if self.banks[bank][ch - 1] == gid:
            return
        self.banks[bank][ch - 1] = gid
        self._changed()

    # ---- discovery -------------------------------------------------------------------
    def import_discovered(self, bulbs: list, positions=None, living_room: str = LIVING_ROOM) -> dict:
        """Merge discovered LIFX bulbs ({label, ip, mac, group}). Known MACs get their IP
        and label refreshed; new Living Room bulbs become active members of their default
        groups, others are listed as not added. `positions(label) -> (x, y)` seeds the room."""
        by_mac = {f.mac: f for f in self.fixtures.values() if f.mac}
        added, updated = [], []
        for b in bulbs:
            mac = (b.get("mac") or "").lower()
            if not MAC.match(mac) or not IP.match(b.get("ip") or ""):
                continue
            label = (b.get("label") or mac).strip()[:48] or mac
            known = by_mac.get(mac)
            if known:                           # the user's name for it wins; refresh the address
                if known.ip != b["ip"] or known.lifx_group != b.get("group"):
                    known.ip, known.lifx_group = b["ip"], b.get("group")
                    updated.append(known.id)
                continue
            living = (b.get("group") or "").lower() == living_room.lower()
            x, y = positions(label) if positions else (0.5, 0.5)
            fx = Fixture(self._unique_id(slugify(label)), label, "lifx",
                         "active" if living else "not_added", "slow",
                         min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y))),
                         ip=b["ip"], mac=mac, lifx_group=b.get("group"),
                         space=self.space_for_group(b.get("group")))
            self.fixtures[fx.id] = fx
            by_mac[mac] = fx
            for gid in default_groups_for(label, b.get("group"), living_room):
                if gid in self.groups and fx.id not in self.groups[gid].members:
                    self.groups[gid].members.append(fx.id)
            added.append(fx.id)
        if added or updated:
            self._changed()
        return {"found": len(bulbs), "added": added, "updated": updated}

    # ---- resolution ------------------------------------------------------------------
    def ordered(self) -> list:
        """Display order (gui-3): LIFX in the rig, LIFX not added, planned, virtual."""
        rank = {("lifx", "active"): 0, ("lifx", "not_added"): 1, ("virtual", "active"): 3}
        return sorted(self.fixtures.values(), key=lambda f: rank.get((f.type, f.status), 2))

    def active(self, type_: Optional[str] = None) -> list:
        return [f for f in self.ordered() if f.status == "active" and (type_ is None or f.type == type_)]

    def groups_of(self, fid: str) -> list:
        return [g for g in self.groups.values() if fid in g.members]

    def members_of(self, gid: str, active_only: bool = True) -> list:
        g = self.groups.get(gid)
        if not g:
            return []
        out = [self.fixtures[m] for m in g.members if m in self.fixtures]
        return [f for f in out if f.status == "active"] if active_only else out

    def limits(self, fid: str) -> tuple:
        """(max_level, max_flash_hz) for a fixture: the most restrictive of its groups (D8)."""
        gs = self.groups_of(fid)
        if not gs:
            return 1.0, MAX_FLASH_HZ
        return min(g.max_level for g in gs), min(min(g.max_flash_hz for g in gs), MAX_FLASH_HZ)
