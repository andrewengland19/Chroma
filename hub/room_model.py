# =============================================================================
# hub/room_model.py — the Scene Agent room model (lighting-scene-agent-spec §4, M1).
#
# Where each light sits comes from $CHROMA_HOME/fixtures.json (the Setup canvas
# x/y, 0..1); this file adds what the canvas can't say: room size, height (z),
# aim, role, limits, plus named anchors and zones (contracts/room.schema.json).
#
#   room.yaml   user-authored, YAML or JSON (JSON is valid YAML)
#   metres      x = fixture.x * dimensions.x, y = fixture.y * dimensions.y,
#               z from the room file (default 1.0). y grows the way the canvas
#               does: y = 0 is the top edge of the Setup floor plan.
#   anchors     a point {position: [x, y, z]} or a wall/ceiling {plane: "y=4.2"}
#               ("ceiling" and "floor" are built in)
#   zones       {within: {anchor, radius}} | {box: {min, max}} | {fixtures: [ids]}
#
# Only active fixtures are in the room. Pure and read-only: nothing here writes
# a file (fixtures.json is parsed directly, not through FixtureStore.load, which
# moves a corrupt file aside).
# =============================================================================

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from fixtures import Fixture, RigError

NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
PLANE = re.compile(r"^\s*([xyz])\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)\s*$")
ROLES = ("ambient", "accent", "task")
CAPABILITIES = ("single", "multizone", "matrix")
KELVIN_MIN, KELVIN_MAX = 1500, 9000
DEFAULT_DIMENSIONS = (5.0, 4.0, 2.6)
DEFAULT_Z = 1.0
AXES = "xyz"


class RoomError(ValueError):
    """A room file (or the fixtures it names) that cannot be used."""


@dataclass(frozen=True)
class Anchor:
    name: str
    point: tuple                    # a point anchor's position, or a plane's centre in the room
    plane: Optional[tuple] = None   # (axis index, at) for a wall / ceiling / floor

    def distance(self, p: tuple) -> float:
        if self.plane:
            return abs(p[self.plane[0]] - self.plane[1])
        return math.dist(p, self.point)


@dataclass(frozen=True)
class Zone:
    name: str
    within: Optional[tuple] = None  # (anchor name, radius m)
    box: Optional[tuple] = None     # ((min...), (max...)), 2 or 3 axes
    members: Optional[tuple] = None  # explicit fixture ids


@dataclass(frozen=True)
class Limits:
    max_brightness: float = 1.0
    kelvin_min: int = KELVIN_MIN
    kelvin_max: int = KELVIN_MAX
    color: bool = True              # False = white-only bulb


@dataclass(frozen=True)
class RoomFixture:
    id: str
    label: str
    position: tuple                 # metres
    aim: object = None              # None | name (str) | unit-ish vector (tuple)
    role: str = "ambient"
    capability: str = "single"
    limits: Limits = field(default_factory=Limits)


@dataclass
class Room:
    name: str
    dimensions: tuple
    viewpoint: Optional[dict]
    default_kelvin: int
    anchors: dict                   # name -> Anchor
    zones: dict                     # name -> Zone
    fixtures: dict                  # id -> RoomFixture, sorted by id

    # ---- places ----------------------------------------------------------------
    def place_point(self, ref) -> tuple:
        """A point for a place reference: [x, y, z], an anchor or a zone (its centre)."""
        if isinstance(ref, (list, tuple)):
            return tuple(float(v) for v in ref)
        if ref in self.anchors:
            return self.anchors[ref].point
        if ref in self.zones:
            return self.zone_center(ref)
        raise RoomError(f"unknown place '{ref}'")

    def distance(self, p: tuple, ref) -> float:
        """Distance (m) from a point to a place; a plane anchor measures straight to the wall."""
        if isinstance(ref, str) and ref in self.anchors:
            return self.anchors[ref].distance(p)
        return math.dist(p, self.place_point(ref))

    def zone_center(self, name: str) -> tuple:
        z = self.zones[name]
        if z.within:
            return self.anchors[z.within[0]].point
        if z.box:
            lo, hi = z.box
            c = [(a + b) / 2 for a, b in zip(lo, hi)]
            return tuple(c + [self.dimensions[2] / 2] * (3 - len(c)))
        pts = [self.fixtures[i].position for i in z.members if i in self.fixtures]
        if not pts:
            raise RoomError(f"zone '{name}' has no fixture in the room")
        return tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))

    def in_zone(self, fixture_id: str, name: str) -> bool:
        z = self.zones[name]
        f = self.fixtures[fixture_id]
        if z.within:
            return self.anchors[z.within[0]].distance(f.position) <= z.within[1]
        if z.box:
            lo, hi = z.box
            return all(lo[i] <= f.position[i] <= hi[i] for i in range(len(lo)))
        return fixture_id in z.members

    def zones_of(self, fixture_id: str) -> list:
        return [n for n in sorted(self.zones) if self.in_zone(fixture_id, n)]


# ---- loading -----------------------------------------------------------------------
def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise RoomError(f"{what} must be a number")
    return float(v)


def _vec(v, what: str, n=(3,)) -> tuple:
    if not isinstance(v, (list, tuple)) or len(v) not in n:
        raise RoomError(f"{what} must be a list of {' or '.join(map(str, n))} numbers")
    return tuple(_num(x, what) for x in v)


def _name(v, what: str) -> str:
    if not isinstance(v, str) or not NAME.match(v):
        raise RoomError(f"{what} must be a lowercase name (a-z, 0-9, _): {v!r}")
    return v


def _anchor(name: str, d, dims: tuple) -> Anchor:
    if not isinstance(d, dict) or len(d) != 1 or not ({"position", "plane"} & set(d)):
        raise RoomError(f"anchor '{name}' needs exactly one of position or plane")
    if "position" in d:
        return Anchor(name, _vec(d["position"], f"anchor '{name}' position"))
    m = PLANE.match(d["plane"]) if isinstance(d["plane"], str) else None
    if not m:
        raise RoomError(f"anchor '{name}' plane must look like \"y=4.2\"")
    axis, at = AXES.index(m.group(1)), float(m.group(2))
    centre = [dims[0] / 2, dims[1] / 2, dims[2] / 2]
    centre[axis] = at
    return Anchor(name, tuple(centre), (axis, at))


def _zone(name: str, d, anchors: dict) -> Zone:
    if not isinstance(d, dict) or len(d) != 1:
        raise RoomError(f"zone '{name}' needs exactly one of within, box or fixtures")
    if "within" in d:
        w = d["within"]
        if not isinstance(w, dict) or set(w) != {"anchor", "radius"}:
            raise RoomError(f"zone '{name}' within needs anchor and radius")
        if w["anchor"] not in anchors:
            raise RoomError(f"zone '{name}' names unknown anchor '{w['anchor']}'")
        r = _num(w["radius"], f"zone '{name}' radius")
        if r <= 0:
            raise RoomError(f"zone '{name}' radius must be > 0")
        return Zone(name, within=(w["anchor"], r))
    if "box" in d:
        b = d["box"]
        if not isinstance(b, dict) or set(b) != {"min", "max"}:
            raise RoomError(f"zone '{name}' box needs min and max")
        lo, hi = _vec(b["min"], f"zone '{name}' min", (2, 3)), _vec(b["max"], f"zone '{name}' max", (2, 3))
        if len(lo) != len(hi) or any(a > c for a, c in zip(lo, hi)):
            raise RoomError(f"zone '{name}' box min must be <= max on the same axes")
        return Zone(name, box=(lo, hi))
    if "fixtures" in d:
        ids = d["fixtures"]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise RoomError(f"zone '{name}' fixtures must be a list of fixture ids")
        return Zone(name, members=tuple(ids))
    raise RoomError(f"zone '{name}' needs exactly one of within, box or fixtures")


def _limits(fid: str, d) -> Limits:
    if d is None:
        return Limits()
    if not isinstance(d, dict) or set(d) - {"max_brightness", "kelvin_min", "kelvin_max", "color"}:
        raise RoomError(f"fixture '{fid}' limits: unknown field")
    mb = _num(d.get("max_brightness", 1.0), f"fixture '{fid}' max_brightness")
    kmin = int(_num(d.get("kelvin_min", KELVIN_MIN), f"fixture '{fid}' kelvin_min"))
    kmax = int(_num(d.get("kelvin_max", KELVIN_MAX), f"fixture '{fid}' kelvin_max"))
    color = d.get("color", True)
    if not 0 <= mb <= 1 or not KELVIN_MIN <= kmin <= kmax <= KELVIN_MAX or not isinstance(color, bool):
        raise RoomError(f"fixture '{fid}' limits out of range")
    return Limits(mb, kmin, kmax, color)


FIXTURE_KEYS = {"z", "aim", "role", "capability", "limits", "exclude"}


def build_room(room: dict, fixtures: list) -> Room:
    """A Room from a parsed room file and fixtures.json's fixture list (dicts)."""
    if not isinstance(room, dict):
        raise RoomError("room file must be a mapping")
    unknown = set(room) - {"v", "room", "units", "dimensions", "viewpoint", "default_kelvin",
                           "anchors", "zones", "fixtures"}
    if unknown:
        raise RoomError(f"room file: unknown field(s) {sorted(unknown)}")
    if room.get("v", 1) != 1 or room.get("units", "meters") != "meters":
        raise RoomError("room file must be v 1, units meters")
    dd = room.get("dimensions") or dict(zip(AXES, DEFAULT_DIMENSIONS))
    if not isinstance(dd, dict) or set(dd) != set(AXES):
        raise RoomError("dimensions needs x, y and z")
    dims = tuple(_num(dd[a], f"dimensions.{a}") for a in AXES)
    if min(dims) <= 0:
        raise RoomError("dimensions must be > 0")

    anchors = {"ceiling": _anchor("ceiling", {"plane": f"z={dims[2]}"}, dims),
               "floor": _anchor("floor", {"plane": "z=0"}, dims)}
    for n, d in (room.get("anchors") or {}).items():
        anchors[_name(n, "anchor name")] = _anchor(n, d, dims)
    zones = {}
    for n, d in (room.get("zones") or {}).items():
        if _name(n, "zone name") in anchors:
            raise RoomError(f"'{n}' is both an anchor and a zone")
        zones[n] = _zone(n, d, anchors)

    vp = room.get("viewpoint")
    if vp is not None:
        if not isinstance(vp, dict) or set(vp) != {"from", "facing"} or not set(vp.values()) <= set(anchors):
            raise RoomError("viewpoint needs from and facing, both anchors")

    dk = int(_num(room.get("default_kelvin", 2700), "default_kelvin"))
    if not KELVIN_MIN <= dk <= KELVIN_MAX:
        raise RoomError("default_kelvin out of range")

    by_id = {}
    for fd in fixtures:
        try:
            fx = Fixture.from_dict(fd)
        except (RigError, TypeError) as e:
            raise RoomError(f"fixtures.json: {e}") from e
        by_id[fx.id] = fx
    extra = room.get("fixtures") or {}
    if not isinstance(extra, dict):
        raise RoomError("fixtures must map fixture id -> fields")
    for fid, fd in extra.items():
        if fid not in by_id:
            raise RoomError(f"fixture '{fid}' is not in fixtures.json")
        if not isinstance(fd, dict) or set(fd) - FIXTURE_KEYS:
            raise RoomError(f"fixture '{fid}': unknown field(s) {sorted(set(fd) - FIXTURE_KEYS)}")

    out = {}
    for fid in sorted(by_id):
        fx, fd = by_id[fid], extra.get(fid) or {}
        if fx.status != "active" or fd.get("exclude"):
            continue
        z = _num(fd.get("z", DEFAULT_Z), f"fixture '{fid}' z")
        aim = fd.get("aim")
        if isinstance(aim, list):
            aim = _vec(aim, f"fixture '{fid}' aim")
            if not any(aim):
                raise RoomError(f"fixture '{fid}' aim vector is zero")
        elif aim is not None and aim not in ("up", "down") and aim not in anchors and aim not in zones:
            raise RoomError(f"fixture '{fid}' aims at unknown '{aim}'")
        role = fd.get("role", "ambient")
        cap = fd.get("capability", "single")
        if role not in ROLES or cap not in CAPABILITIES:
            raise RoomError(f"fixture '{fid}': role must be one of {ROLES}, capability one of {CAPABILITIES}")
        out[fid] = RoomFixture(fid, fx.label, (fx.x * dims[0], fx.y * dims[1], z), aim, role, cap,
                               _limits(fid, fd.get("limits")))

    r = Room(str(room.get("room", "living_room")), dims, vp, dk, anchors, zones, out)
    for n, zn in zones.items():
        for i in zn.members or ():
            if i not in by_id:
                raise RoomError(f"zone '{n}' names unknown fixture '{i}'")
    return r


def default_paths() -> tuple:
    """(room file, fixtures.json) under $CHROMA_HOME."""
    from paths import chroma_dir
    home = chroma_dir()
    return os.path.join(home, "scene-agent", "room.yaml"), os.path.join(home, "fixtures.json")


def load_room(room_path: str, fixtures_path: str) -> Room:
    """Read room.yaml (YAML or JSON; missing = defaults, no anchors or zones) and fixtures.json."""
    room = {}
    if os.path.exists(room_path):
        with open(room_path) as f:
            text = f.read()
        try:
            import yaml   # PyYAML ships with uvicorn[standard]; JSON needs nothing extra
            room = yaml.safe_load(text) or {}
        except ImportError:
            room = json.loads(text)
        except Exception as e:      # yaml.YAMLError without importing it at module level
            raise RoomError(f"{room_path}: {e}") from e
    try:
        with open(fixtures_path) as f:
            fixtures = json.load(f).get("fixtures", [])
    except (OSError, ValueError, AttributeError) as e:
        raise RoomError(f"{fixtures_path}: {e}") from e
    return build_room(room, fixtures)
