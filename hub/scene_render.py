# =============================================================================
# hub/scene_render.py — the Scene Agent renderer (lighting-scene-agent-spec §6–7.1, M1).
#
#   render(room, spec, snapshots) -> table      pure and deterministic; writes nothing
#
# Each fixture starts from the base snapshot (or off), then every layer in order
# computes a weight for it from where it is (or where its aim lands) and blends
# its colour/brightness in. Colours blend in OKLab (hub/oklab.py) and become
# device hue/sat/brightness/Kelvin only at the end. Whites stay whites: a Kelvin
# value with zero saturation. Last, each fixture is clamped to its limits
# (max brightness, Kelvin range, white-only) and the table marks where that
# changed the result noticeably.
#
#   snapshot / table entry   {hue: deg, sat: 0..1, brightness: 0..1, kelvin: int}
#                            (a table entry adds hex; a rendered table is a valid snapshot)
#
# validate_spec() mirrors contracts/scene-spec.schema.json by hand (no jsonschema at
# runtime); the tests check both reject the same bad-* examples.
# =============================================================================

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from typing import Optional

import oklab
from room_model import KELVIN_MAX, KELVIN_MIN, NAME, ROLES, Room, RoomError

WHITE_SAT = 0.001          # at or below this a colour is a white
AIM_CONE_DEG = 30.0     # an aim vector "lands on" a point anchor within this angle
NOTICE_BRI, NOTICE_K, NOTICE_SAT = 0.005, 25, 0.05


class SpecError(ValueError):
    """A SceneSpec that cannot be rendered (shown to the model / user)."""


# ---- colour ------------------------------------------------------------------------
def kelvin_rgb(k: float) -> tuple:
    """sRGB 0..1 of a blackbody white (Tanner Helland's fit)."""
    t = k / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255.0 if t >= 66 else 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    return tuple(min(255.0, max(0.0, c)) / 255.0 for c in (r, g, b))


def kelvin_lab(k: float) -> tuple:
    return oklab.rgb_to_oklab(kelvin_rgb(k))


def _lerp(a, b, t: float):
    if isinstance(a, tuple):
        return tuple(x * (1 - t) + y * t for x, y in zip(a, b))
    return a * (1 - t) + b * t


@dataclass(frozen=True)
class Color:
    lab: tuple
    kelvin: Optional[float] = None      # set = a white

    @classmethod
    def from_spec(cls, c: dict) -> "Color":
        if "kelvin" in c:
            return cls(kelvin_lab(c["kelvin"]), float(c["kelvin"]))
        return cls(oklab.chroma_lab(c["hue_deg"], c["sat"]))

    def mix(self, other: "Color", t: float) -> "Color":
        if self.kelvin is not None and other.kelvin is not None:
            k = _lerp(self.kelvin, other.kelvin, t)
            return Color(kelvin_lab(k), k)
        return Color(_lerp(self.lab, other.lab, t))


@dataclass
class Light:
    lab: tuple
    bri: float
    kelvin: float
    white: bool

    @classmethod
    def off(cls, kelvin: float) -> "Light":
        return cls(kelvin_lab(kelvin), 0.0, float(kelvin), True)

    @classmethod
    def from_state(cls, d: dict) -> "Light":
        k = float(d.get("kelvin", 2700))
        if d.get("sat", 0) <= WHITE_SAT:
            return cls(kelvin_lab(k), float(d["brightness"]), k, True)
        return cls(oklab.chroma_lab(d["hue"], d["sat"]), float(d["brightness"]), k, False)

    def hue_sat(self) -> tuple:
        if self.white:
            return 0.0, 0.0
        h, s, _v = colorsys.rgb_to_hsv(*oklab.oklab_to_rgb(self.lab))
        return (h * 360.0) % 360.0, s

    def paint(self, col: Optional[Color], bri: Optional[float], w: float) -> None:
        """Blend a layer in at weight w. Colour mixes like light does: weighted by each
        side's brightness, so a pool on an off bulb takes the pool's colour outright."""
        if w <= 0:
            return
        cw = w
        if bri is not None:
            denom = w * bri + (1 - w) * self.bri
            cw = w * bri / denom if denom > 0 else w
            self.bri = _lerp(self.bri, bri, w)
        if col is None or cw <= 0:
            return
        if col.kelvin is not None and self.white:
            self.kelvin = _lerp(self.kelvin, col.kelvin, cw)
            self.lab = kelvin_lab(self.kelvin)
        elif cw >= 1:
            self.lab, self.white = col.lab, col.kelvin is not None
            if col.kelvin is not None:
                self.kelvin = col.kelvin
        else:
            self.lab, self.white = _lerp(self.lab, col.lab, cw), False
            if col.kelvin is not None:
                self.kelvin = _lerp(self.kelvin, col.kelvin, cw)

    def set_kelvin(self, k: float) -> None:
        k = min(KELVIN_MAX, max(KELVIN_MIN, k))
        if self.white:
            self.lab = kelvin_lab(k)
        else:   # the same white-balance tint, applied to the colour
            old, new = kelvin_lab(self.kelvin), kelvin_lab(k)
            self.lab = tuple(c + n - o for c, n, o in zip(self.lab, new, old))
        self.kelvin = k

    def to_state(self) -> dict:
        h, s = self.hue_sat()
        if self.white:
            rgb = kelvin_rgb(self.kelvin)
        else:
            rgb = colorsys.hsv_to_rgb(h / 360.0, s, 1.0)
        hexs = "#%02x%02x%02x" % tuple(round(min(1.0, max(0.0, c * self.bri)) * 255) for c in rgb)
        return {"hue": round(h, 2), "sat": round(s, 4), "brightness": round(self.bri, 4),
                "kelvin": int(round(self.kelvin)), "hex": hexs}


# ---- validation (mirrors contracts/scene-spec.schema.json) --------------------------
PAINT = {"color", "brightness"}
OPS = {   # op -> (required, optional)
    "fill": (set(), {"target", "color", "brightness", "opacity"}),
    "pool": ({"center", "radius_m"}, {"target", "falloff", "color", "brightness", "opacity"}),
    "gradient": ({"from", "to", "colors"}, {"target", "brightness", "opacity"}),
    "wash": ({"surface"}, {"target", "color", "brightness", "opacity"}),
    "scale": ({"brightness_factor"}, {"target", "opacity"}),
    "shift": (set(), {"target", "hue_deg", "sat_delta", "kelvin_delta", "opacity"}),
    "accent": ({"at"}, {"target", "count", "color", "brightness", "opacity"}),
}
TARGET_KEYS = {"all", "fixture", "zone", "role", "aim", "height_above", "height_below"}


def _isnum(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _range(v, lo, hi, what, integer=False):
    if not _isnum(v) or (integer and not float(v).is_integer()) or not lo <= v <= hi:
        raise SpecError(f"{what} must be a{'n integer' if integer else ' number'} in [{lo}, {hi}]")


def _check_color(c, what):
    if not isinstance(c, dict):
        raise SpecError(f"{what} must be {{kelvin}} or {{hue_deg, sat}}")
    if set(c) == {"kelvin"}:
        _range(c["kelvin"], KELVIN_MIN, KELVIN_MAX, f"{what}.kelvin", integer=True)
    elif set(c) == {"hue_deg", "sat"}:
        _range(c["hue_deg"], 0, 360, f"{what}.hue_deg")
        if c["hue_deg"] >= 360:
            raise SpecError(f"{what}.hue_deg must be < 360")
        _range(c["sat"], 0, 1, f"{what}.sat")
    else:
        raise SpecError(f"{what} must be {{kelvin}} or {{hue_deg, sat}}")


def _check_place(p, what):
    if isinstance(p, str):
        if not NAME.match(p):
            raise SpecError(f"{what} must be an anchor/zone name or [x, y, z]")
    elif isinstance(p, list) and len(p) == 3:
        for v in p:
            _range(v, 0, 100, what)
    else:
        raise SpecError(f"{what} must be an anchor/zone name or [x, y, z]")


def _check_target(t, what):
    import re
    if not isinstance(t, dict) or not t or set(t) - TARGET_KEYS:
        raise SpecError(f"{what} must use only {sorted(TARGET_KEYS)}")
    if "all" in t and t["all"] is not True:
        raise SpecError(f"{what}.all must be true")
    if "fixture" in t and not (isinstance(t["fixture"], str) and re.match(r"^[a-z0-9][a-z0-9-]{0,47}$", t["fixture"])):
        raise SpecError(f"{what}.fixture is not a fixture id")
    for k in ("zone", "aim"):
        if k in t and not (isinstance(t[k], str) and NAME.match(t[k])):
            raise SpecError(f"{what}.{k} is not a name")
    if "role" in t and t["role"] not in ROLES:
        raise SpecError(f"{what}.role must be one of {ROLES}")
    for k in ("height_above", "height_below"):
        if k in t:
            _range(t[k], 0, 100, f"{what}.{k}")


def validate_spec(spec) -> None:
    """Raise SpecError unless spec matches contracts/scene-spec.schema.json."""
    import re
    if not isinstance(spec, dict) or set(spec) - {"name", "summary", "base", "layers", "transition_s"}:
        raise SpecError("spec may only have name, summary, base, layers, transition_s")
    if not isinstance(spec.get("name"), str) or not re.match(r"^agent\.[a-z0-9][a-z0-9_-]{0,47}$", spec["name"]):
        raise SpecError("name must be agent.<slug>")
    if "summary" in spec and not (isinstance(spec["summary"], str) and len(spec["summary"]) <= 280):
        raise SpecError("summary must be a string of at most 280 characters")
    if "base" in spec:
        b = spec["base"]
        if not isinstance(b, dict) or set(b) != {"scene", "mode"} or b["mode"] != "start_from" \
                or not isinstance(b["scene"], str) or not re.match(r"^[A-Za-z0-9_.-]{1,64}$", b["scene"]):
            raise SpecError("base must be {scene: <name>, mode: start_from}")
    if "transition_s" in spec:
        _range(spec["transition_s"], 0, 60, "transition_s")
    layers = spec.get("layers")
    if not isinstance(layers, list) or len(layers) > 32:
        raise SpecError("layers must be a list of at most 32 layers")
    for i, L in enumerate(layers):
        what = f"layers[{i}]"
        if not isinstance(L, dict) or L.get("op") not in OPS:
            raise SpecError(f"{what}.op must be one of {sorted(OPS)}")
        req, opt = OPS[L["op"]]
        keys = set(L) - {"op"}
        if req - keys or keys - req - opt:
            raise SpecError(f"{what} ({L['op']}) needs {sorted(req)} and may have {sorted(opt)}")
        if (PAINT & opt) == PAINT and not (PAINT & keys):
            raise SpecError(f"{what} ({L['op']}) needs color or brightness")
        if L["op"] == "shift" and not ({"hue_deg", "sat_delta", "kelvin_delta"} & keys):
            raise SpecError(f"{what} (shift) needs hue_deg, sat_delta or kelvin_delta")
        if "target" in L:
            _check_target(L["target"], f"{what}.target")
        if "color" in L:
            _check_color(L["color"], f"{what}.color")
        for k in ("brightness", "opacity"):
            if k in L:
                _range(L[k], 0, 1, f"{what}.{k}")
        for k in ("center", "from", "to", "at"):
            if k in L:
                _check_place(L[k], f"{what}.{k}")
        if "surface" in L and not (isinstance(L["surface"], str) and NAME.match(L["surface"])):
            raise SpecError(f"{what}.surface must be an anchor or zone name")
        if "radius_m" in L:
            _range(L["radius_m"], 0, 20, f"{what}.radius_m")
            if L["radius_m"] <= 0:
                raise SpecError(f"{what}.radius_m must be > 0")
        if "falloff" in L and L["falloff"] not in ("smooth", "linear", "hard"):
            raise SpecError(f"{what}.falloff must be smooth, linear or hard")
        if "colors" in L:
            if not isinstance(L["colors"], list) or not 2 <= len(L["colors"]) <= 8:
                raise SpecError(f"{what}.colors needs 2 to 8 colours")
            for j, c in enumerate(L["colors"]):
                _check_color(c, f"{what}.colors[{j}]")
        if "brightness_factor" in L:
            _range(L["brightness_factor"], 0, 4, f"{what}.brightness_factor")
        if "hue_deg" in L and L["op"] == "shift":
            _range(L["hue_deg"], -180, 180, f"{what}.hue_deg")
        if "sat_delta" in L:
            _range(L["sat_delta"], -1, 1, f"{what}.sat_delta")
        if "kelvin_delta" in L:
            _range(L["kelvin_delta"], -6000, 6000, f"{what}.kelvin_delta", integer=True)
        if "count" in L:
            _range(L["count"], 1, 4, f"{what}.count", integer=True)


# ---- spatial weights ---------------------------------------------------------------
def _targets(room: Room, t: Optional[dict], what: str) -> dict:
    """fixture id -> True for the fixtures a target selects (all when absent)."""
    t = t or {}
    if "fixture" in t and t["fixture"] not in room.fixtures:
        raise SpecError(f"{what}: unknown fixture '{t['fixture']}'")
    if "zone" in t and t["zone"] not in room.zones:
        raise SpecError(f"{what}: unknown zone '{t['zone']}'")
    out = {}
    for fid, f in room.fixtures.items():
        ok = ("fixture" not in t or t["fixture"] == fid) \
            and ("zone" not in t or room.in_zone(fid, t["zone"])) \
            and ("role" not in t or f.role == t["role"]) \
            and ("aim" not in t or f.aim == t["aim"]) \
            and ("height_above" not in t or f.position[2] > t["height_above"]) \
            and ("height_below" not in t or f.position[2] < t["height_below"])
        out[fid] = ok
    return out


def _place(room: Room, ref, what: str):
    if isinstance(ref, str) and ref not in room.anchors and ref not in room.zones:
        raise SpecError(f"{what}: unknown anchor or zone '{ref}'")
    try:
        room.place_point(ref)
    except RoomError as e:
        raise SpecError(f"{what}: {e}") from e
    return ref


def _falloff(d: float, r: float, kind: str) -> float:
    t = d / r
    if t >= 1:
        return 1.0 if kind == "hard" and t == 1 else 0.0
    if kind == "hard":
        return 1.0
    if kind == "linear":
        return 1.0 - t
    return 1.0 - t * t * (3 - 2 * t)          # smoothstep, 1 at the centre


def _aim_vector(aim) -> Optional[tuple]:
    if aim == "up":
        return (0.0, 0.0, 1.0)
    if aim == "down":
        return (0.0, 0.0, -1.0)
    return aim if isinstance(aim, tuple) else None


def _lands_on(room: Room, f, surface: str) -> bool:
    """Does this fixture's aim land on the surface (an anchor or zone)?"""
    if isinstance(f.aim, str) and f.aim == surface:
        return True
    v = _aim_vector(f.aim)
    a = room.anchors.get(surface)
    if v is None or a is None:
        return False
    p = f.position
    if a.plane:
        axis, at = a.plane
        if abs(v[axis]) < 1e-9:
            return False
        s = (at - p[axis]) / v[axis]
        if s <= 0:
            return False
        hit = [p[i] + s * v[i] for i in range(3)]
        return all(-0.05 <= hit[i] <= room.dimensions[i] + 0.05 for i in range(3) if i != axis)
    to = tuple(a.point[i] - p[i] for i in range(3))
    n1, n2 = math.hypot(*v), math.hypot(*to)
    if n2 < 1e-9:
        return True
    cos = sum(x * y for x, y in zip(v, to)) / (n1 * n2)
    return cos >= math.cos(math.radians(AIM_CONE_DEG))


# ---- render ------------------------------------------------------------------------
def render(room: Room, spec: dict, snapshots: Optional[dict] = None) -> dict:
    """Render a SceneSpec to {name, transition_s, fixtures: {id: state}, clamped: {id: [why]},
    warnings: [...]}. Raises SpecError for an invalid spec or an unknown name/zone/anchor/scene."""
    validate_spec(spec)
    warnings = []
    lights = {fid: Light.off(room.default_kelvin) for fid in room.fixtures}

    base = spec.get("base")
    if base:
        snap = (snapshots or {}).get(base["scene"])
        if snap is None:
            raise SpecError(f"no snapshot for base scene '{base['scene']}'")
        for fid in sorted(snap):
            if fid not in room.fixtures:
                warnings.append(f"base '{base['scene']}': fixture '{fid}' is not in the room; ignored")
        for fid in room.fixtures:
            if fid in snap:
                lights[fid] = Light.from_state(snap[fid])
            else:
                warnings.append(f"base '{base['scene']}' has no '{fid}'; it starts off")

    for i, L in enumerate(spec["layers"]):
        _apply(room, L, f"layers[{i}] ({L['op']})", lights, warnings)

    table, clamped = {}, {}
    for fid, f in room.fixtures.items():
        why = _clamp(lights[fid], f.limits)
        if why:
            clamped[fid] = why
        table[fid] = lights[fid].to_state()
    return {"name": spec["name"], "transition_s": spec.get("transition_s", 0),
            "fixtures": table, "clamped": clamped, "warnings": warnings}


def _apply(room: Room, L: dict, what: str, lights: dict, warnings: list) -> None:
    op, opacity = L["op"], L.get("opacity", 1.0)
    sel = _targets(room, L.get("target"), what)
    col = Color.from_spec(L["color"]) if "color" in L else None
    bri = L.get("brightness")
    fx = room.fixtures

    if op == "fill":
        for fid in fx:
            if sel[fid]:
                lights[fid].paint(col, bri, opacity)

    elif op == "pool":
        c = _place(room, L["center"], what)
        for fid, f in fx.items():
            if sel[fid]:
                w = _falloff(room.distance(f.position, c), L["radius_m"], L.get("falloff", "smooth"))
                lights[fid].paint(col, bri, w * opacity)

    elif op == "gradient":
        a = room.place_point(_place(room, L["from"], what))
        b = room.place_point(_place(room, L["to"], what))
        ab = tuple(y - x for x, y in zip(a, b))
        n2 = sum(v * v for v in ab)
        if n2 < 1e-9:
            raise SpecError(f"{what}: from and to are the same place")
        stops = [Color.from_spec(c) for c in L["colors"]]
        for fid, f in fx.items():
            if not sel[fid]:
                continue
            t = sum((p - x) * v for p, x, v in zip(f.position, a, ab)) / n2
            seg = min(1.0, max(0.0, t)) * (len(stops) - 1)
            j = min(int(seg), len(stops) - 2)
            lights[fid].paint(stops[j].mix(stops[j + 1], seg - j), bri, opacity)

    elif op == "wash":
        s = L["surface"]
        if s not in room.anchors and s not in room.zones:
            raise SpecError(f"{what}: unknown surface '{s}'")
        hit = [fid for fid, f in fx.items() if sel[fid] and _lands_on(room, f, s)]
        if not hit:
            warnings.append(f"{what}: no fixture aims at '{s}'")
        for fid in hit:
            lights[fid].paint(col, bri, opacity)

    elif op == "scale":
        k = L["brightness_factor"]
        for fid in fx:
            if sel[fid]:
                lights[fid].bri *= 1 + opacity * (k - 1)

    elif op == "shift":
        skipped = []
        for fid in fx:
            if not sel[fid]:
                continue
            lt = lights[fid]
            if "kelvin_delta" in L:
                lt.set_kelvin(lt.kelvin + L["kelvin_delta"] * opacity)
            if "hue_deg" in L or "sat_delta" in L:
                if lt.white:
                    skipped.append(fid)
                    continue
                h, s = lt.hue_sat()
                h = (h + L.get("hue_deg", 0) * opacity) % 360.0
                s = min(1.0, max(0.0, s + L.get("sat_delta", 0) * opacity))
                if s <= WHITE_SAT:
                    lt.white, lt.lab = True, kelvin_lab(lt.kelvin)
                else:
                    lt.lab = oklab.chroma_lab(h, s)
        if skipped:
            warnings.append(f"{what}: hue/sat has no effect on white fixtures {skipped}")

    elif op == "accent":
        at = _place(room, L["at"], what)
        near = sorted((room.distance(f.position, at), fid) for fid, f in fx.items() if sel[fid])
        for _d, fid in near[:L.get("count", 1)]:
            lights[fid].paint(col, bri, opacity)

    if not any(sel.values()):
        warnings.append(f"{what}: target matches no fixture")


def _clamp(lt: Light, lim) -> list:
    """Clamp to what the fixture can do; return why, if it changed noticeably."""
    why = []
    if lt.bri > lim.max_brightness:
        if lt.bri - lim.max_brightness > NOTICE_BRI:
            why.append(f"brightness capped at {lim.max_brightness}")
        lt.bri = lim.max_brightness
    lt.bri = min(1.0, max(0.0, lt.bri))
    if not lim.color and not lt.white:
        if lt.hue_sat()[1] > NOTICE_SAT:
            why.append("white-only: colour dropped")
        lt.white = True
    k = min(lim.kelvin_max, max(lim.kelvin_min, lt.kelvin))
    if k != lt.kelvin:
        if lt.white and abs(k - lt.kelvin) > NOTICE_K:
            why.append(f"kelvin clamped to {k}")
        lt.kelvin = k
    if lt.white:
        lt.lab = kelvin_lab(lt.kelvin)
    return why
