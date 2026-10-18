# =============================================================================
# hub/scene_taste.py — the Taste Profile (lighting-scene-agent-spec §5.4, M2).
#
#   generate_profile(snapshots)   statistics over the user's own saved scenes
#   apply_overrides(profile, ov)  hand edits win over the generated numbers
#   prompt_text(profile)          plain statistics for the model's context ("" = switched off)
#   read/write_profile            registry/taste_profile.json; `overrides` survive regeneration
#
# Inputs are snapshots (contracts/snapshot.schema.json). agent.* scenes and non-static scenes
# are left out: the profile describes taste, not what the agent already made (DECISIONS).
# Pure apart from read/write_profile.
# =============================================================================

from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timezone
from typing import Optional

from scene_registry import AGENT_PREFIX

RANGE_KEYS = ("min", "median", "max")


def _range(vals: list, ndigits: int = 4, as_int: bool = False) -> Optional[dict]:
    if not vals:
        return None
    r = {"min": min(vals), "median": statistics.median(vals), "max": max(vals)}
    return {k: int(round(v)) if as_int else round(v, ndigits) for k, v in r.items()}


def eligible(snapshots: dict) -> dict:
    return {n: s for n, s in snapshots.items()
            if not n.startswith(AGENT_PREFIX) and s.get("static", True) and s["descriptors"]["lit_count"] > 0}


def generate_profile(snapshots: dict, overrides: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    use = eligible(snapshots)
    d = [use[n]["descriptors"] for n in sorted(use)]
    fams: dict = {}
    for x in d:
        fams[x["dominant_hue"]] = fams.get(x["dominant_hue"], 0) + 1
    p = {"v": 1, "generated_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "scenes": sorted(use), "scene_count": len(use), "strength": 1,
         "brightness": _range([x["mean_brightness"] for x in d]),
         "peak_brightness": max((x["peak_brightness"] for x in d), default=None),
         "kelvin": _range([x["kelvin_mean"] for x in d if x["kelvin_mean"] is not None], as_int=True),
         "hues": [{"family": f, "share": round(c / len(d), 4)}
                  for f, c in sorted(fams.items(), key=lambda kv: (-kv[1], kv[0]))],
         "contrast": _range([x["zone_contrast"] for x in d])}
    return {**p, "overrides": overrides} if overrides else p


def apply_overrides(profile: dict, overrides: Optional[dict] = None) -> dict:
    """The effective profile: generated numbers with the user's overrides laid on top."""
    ov = overrides if overrides is not None else profile.get("overrides") or {}
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in profile.items() if k != "overrides"}
    if "strength" in ov:
        out["strength"] = ov["strength"]
    for k in ("brightness", "kelvin", "contrast"):
        if k in ov:
            out[k] = {**(out.get(k) or {}), **ov[k]}
    return out


def prompt_text(profile: dict, enabled: bool = True) -> str:
    """The statistics as plain lines for the model; "" when switched off or there is nothing."""
    p = apply_overrides(profile)
    if not enabled or p["scene_count"] == 0 or p["strength"] <= 0:
        return ""
    def pct(r):
        return f"{r['min']:.0%} to {r['max']:.0%} (typically {r['median']:.0%})"
    lines = [f"Taste profile from {p['scene_count']} of the user's saved scenes "
             f"(lean on it with strength {p['strength']:g} of 1; soft defaults, not limits):"]
    if p.get("brightness"):
        lines.append(f"- Scene brightness (mean of lit lights): {pct(p['brightness'])}; "
                     f"no single light above {p['peak_brightness']:.0%}.")
    if p.get("kelvin"):
        k = p["kelvin"]
        lines.append(f"- White colour temperature: {k['min']}K to {k['max']}K (typically {k['median']}K).")
    if p["hues"]:
        lines.append("- Dominant colours: " + ", ".join(f"{h['family']} {h['share']:.0%}" for h in p["hues"][:4]) + " of scenes.")
    if p.get("contrast"):
        lines.append(f"- Contrast between zones: {pct(p['contrast'])}.")
    return "\n".join(lines)


def read_profile(path: str) -> Optional[dict]:
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def write_profile(path: str, profile: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(profile, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def refresh_profile(path: str, snapshots: dict, now: Optional[datetime] = None) -> dict:
    """Regenerate the file at `path`, keeping the user's overrides."""
    old = read_profile(path) or {}
    p = generate_profile(snapshots, old.get("overrides") or None, now)
    write_profile(path, p)
    return p
