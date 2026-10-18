# =============================================================================
# hub/scene_normalize.py — scene normalization (lighting-scene-agent-spec §5.3, M2).
#
# Turns what a scene looks like on the bulbs (raw LIFX HSBK, 16-bit) into the room
# model's terms: {fixture id: {hue, sat, brightness, kelvin}} (the renderer's
# snapshot shape) plus descriptors computed by code (contracts/snapshot.schema.json).
# Pure: no files, no bulbs.
#
#   hsbk_state(h, s, b, k)         one raw HSBK -> state. Raw sat under WHITE_RAW is a
#                                  white (lite's own cut, lights_agent.scenes_view).
#   normalize_lights(room, lights) lite scene entries [{label, ip, h, s, b, k}] -> table.
#                                  Matched to fixtures by ip, then label; room fixtures the
#                                  scene does not list are off (a recall turns the rest off).
#   normalize_raw(raw)             {fixture id: {power, hsbk}} read back from bulbs -> table.
#   describe(room, table)          the derived descriptors.
#   states_close(a, b)             tolerance compare (read-back vs expected, static check).
# =============================================================================

from __future__ import annotations

import math
from typing import Optional

from room_model import KELVIN_MAX, KELVIN_MIN, Room

WHITE_RAW = 3000            # raw sat (of 65535) under which a lite scene entry is a white
RAW_MAX = 65535.0
COLOR_SAT = 0.15            # a lit fixture at least this saturated counts as coloured
HUE_FAMILIES = (            # (start deg, family); a family runs to the next start, wrapping
    (15, "orange"), (45, "yellow"), (70, "green"), (165, "cyan"), (200, "blue"),
    (260, "purple"), (300, "magenta"), (345, "red"),
)
TOL = {"brightness": 0.02, "sat": 0.03, "kelvin": 50, "hue": 4.0}


def hue_family(h: float) -> str:
    h %= 360.0
    fam = "red"      # [345, 360) and [0, 15)
    for start, name in HUE_FAMILIES:
        if h >= start:
            fam = name
    return fam


def hsbk_state(h: float, s: float, b: float, k: float, power: bool = True) -> dict:
    """Raw 16-bit HSBK (kelvin as kelvin) -> a snapshot state. Powered off = brightness 0."""
    white = s < WHITE_RAW
    return {"hue": 0.0 if white else round((h / RAW_MAX * 360.0) % 360.0, 2),
            "sat": 0.0 if white else round(min(1.0, s / RAW_MAX), 4),
            "brightness": round(min(1.0, max(0.0, b / RAW_MAX)), 4) if power else 0.0,
            "kelvin": int(min(KELVIN_MAX, max(KELVIN_MIN, round(k))))}


def off_state(kelvin: int) -> dict:
    return {"hue": 0.0, "sat": 0.0, "brightness": 0.0, "kelvin": int(kelvin)}


def normalize_raw(raw: dict) -> dict:
    """{fixture id: {"power": bool, "hsbk": (h, s, b, k)}} -> {fixture id: state}."""
    return {fid: hsbk_state(*d["hsbk"], power=bool(d.get("power", True))) for fid, d in sorted(raw.items())}


def normalize_lights(room: Room, lights: list, ips: Optional[dict] = None) -> tuple:
    """A lite scene's `lights` -> (table, unmatched). `ips` maps fixture id -> ip (room fixtures
    carry only labels). Non-LIFX entries (no `ip`, e.g. Home Assistant) and entries that match no
    room fixture are returned in `unmatched` as their label/ip."""
    ips = ips or {}
    by_ip = {ip: fid for fid, ip in ips.items() if ip}
    by_label = {f.label.casefold(): fid for fid, f in room.fixtures.items()}
    table, unmatched = {}, []
    for e in lights:
        if not isinstance(e, dict) or "ip" not in e or not all(k in e for k in "hsbk"):
            unmatched.append(str(e.get("label", "?")) if isinstance(e, dict) else "?")
            continue
        fid = by_ip.get(e["ip"]) or by_label.get(str(e.get("label", "")).casefold())
        if fid is None or fid not in room.fixtures:
            unmatched.append(str(e.get("label") or e["ip"]))
            continue
        table[fid] = hsbk_state(e["h"], e["s"], e["b"], e["k"])
    for fid in room.fixtures:
        table.setdefault(fid, off_state(room.default_kelvin))
    return dict(sorted(table.items())), sorted(unmatched)


def states_close(a: dict, b: dict) -> bool:
    if abs(a["brightness"] - b["brightness"]) > TOL["brightness"]:
        return False
    if a["brightness"] <= 0 and b["brightness"] <= 0:
        return True                     # both dark: colour is invisible
    if abs(a["sat"] - b["sat"]) > TOL["sat"]:
        return False
    if a["sat"] > COLOR_SAT or b["sat"] > COLOR_SAT:
        dh = abs(a["hue"] - b["hue"]) % 360.0
        if min(dh, 360.0 - dh) > TOL["hue"]:
            return False
    return abs(a["kelvin"] - b["kelvin"]) <= TOL["kelvin"] or a["sat"] > COLOR_SAT


def tables_close(a: dict, b: dict) -> list:
    """Fixture ids (sorted) whose states differ between two tables, or that only one has."""
    return sorted(f for f in set(a) | set(b) if f not in a or f not in b or not states_close(a[f], b[f]))


# ---- descriptors -------------------------------------------------------------------
def _basis(room: Room) -> tuple:
    """(origin xy, forward unit xy, right unit xy). Forward = from the viewpoint anchor toward its
    focal anchor; right is clockwise from forward on the Setup canvas (y grows downward). With no
    viewpoint: from the room centre toward y = 0 (the top of the canvas)."""
    vp = room.viewpoint
    if vp:
        o = room.anchors[vp["from"]].point
        t = room.anchors[vp["facing"]].point
        f = (t[0] - o[0], t[1] - o[1])
        if math.hypot(*f) > 1e-6:
            n = math.hypot(*f)
            fwd = (f[0] / n, f[1] / n)
            return (o[0], o[1]), fwd, (-fwd[1], fwd[0])
        return (o[0], o[1]), (0.0, -1.0), (1.0, 0.0)
    return (room.dimensions[0] / 2, room.dimensions[1] / 2), (0.0, -1.0), (1.0, 0.0)


def _balance(room: Room, table: dict, axis: tuple, origin: tuple) -> float:
    """Brightness-weighted offset along an axis, scaled by the farthest fixture: -1..1."""
    proj = {fid: (f.position[0] - origin[0]) * axis[0] + (f.position[1] - origin[1]) * axis[1]
            for fid, f in room.fixtures.items() if fid in table}
    total = sum(table[f]["brightness"] for f in proj)
    reach = max((abs(p) for p in proj.values()), default=0.0)
    if total <= 0 or reach < 1e-9:
        return 0.0
    return round(max(-1.0, min(1.0, sum(table[f]["brightness"] * p for f, p in proj.items()) / (total * reach))), 3)


def _contrast(room: Room, table: dict) -> float:
    def mean(ids):
        return sum(table[i]["brightness"] for i in ids) / len(ids)
    zones = [mean(ids) for n in room.zones
             if (ids := [i for i in room.fixtures if i in table and room.in_zone(i, n)])]
    vals = zones if len(zones) >= 2 else [table[i]["brightness"] for i in room.fixtures if i in table]
    return round(max(vals) - min(vals), 4) if len(vals) >= 2 else 0.0


def describe(room: Room, table: dict) -> dict:
    """Descriptors for a table (schema: snapshot.schema.json#/$defs/descriptors)."""
    lit = {f: s for f, s in table.items() if s["brightness"] > 0 and f in room.fixtures}
    whites = [s["kelvin"] for s in lit.values() if s["sat"] <= COLOR_SAT]
    weight = {}
    for s in lit.values():
        if s["sat"] > COLOR_SAT:
            fam = hue_family(s["hue"])
            weight[fam] = weight.get(fam, 0.0) + s["brightness"]
    dominant = None
    if lit:
        dominant = min(weight, key=lambda k: (-weight[k], k)) if weight else "white"
    origin, fwd, right = _basis(room)
    return {
        "lit_count": len(lit),
        "mean_brightness": round(sum(s["brightness"] for s in lit.values()) / len(lit), 4) if lit else 0.0,
        "peak_brightness": round(max((s["brightness"] for s in lit.values()), default=0.0), 4),
        "kelvin_mean": round(sum(whites) / len(whites), 1) if whites else None,
        "kelvin_min": min(whites) if whites else None,
        "kelvin_max": max(whites) if whites else None,
        "dominant_hue": dominant,
        "lr_balance": _balance(room, table, right, origin),
        "fb_balance": _balance(room, table, fwd, origin),
        "zone_contrast": _contrast(room, table),
    }
