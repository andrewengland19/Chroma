#!/usr/bin/env python3
# =============================================================================
# hub/lights_agent.py  —  Pass 5: natural-language light control.
#
# Describe how the room should look; Claude (Sonnet) drives the bulbs through a
# small tool set. Shares lite's files so CLI and agent see the same world:
#   ~/.lifx_cli_cache.json   bulbs (label/group/mac/ip)
#   ~/.spectrum_scenes.json  scenes (create-only from here — never overwrites)
#   ~/Code/Spectrum/patterns YAML patterns
# Every run snapshots the room first → `undo()` puts it back.
# Inside `scoped(macs, names)` (the app's Spaces) every tool only sees those bulbs,
# so "all", recall's "turn off the rest", saves and the undo snapshot stay in the
# chosen rooms. Without it (POST /agent, ⌃⇧L, the CLI) the whole house is in play.
#
# Used by server.py (POST /agent, POST /agent/undo) and directly:
#   ./.venv/bin/python lights_agent.py "rainy sunday, cozy but I need to read"
#   ./.venv/bin/python lights_agent.py --undo
# =============================================================================

from __future__ import annotations

import colorsys
import json
import os
import sys
import time
from contextlib import contextmanager

from keystore import load_anthropic_key

MODEL = "claude-sonnet-5-5"
CACHE_PATH = os.path.expanduser("~/.lifx_cli_cache.json")
SCENES_PATH = os.path.expanduser("~/.spectrum_scenes.json")
LAYOUT_PATH = os.path.expanduser("~/.chroma/layout.json")
UNDO_PATH = os.path.expanduser("~/.chroma/lights_undo.json")
SPECTRUM_DIR = os.path.expanduser("~/Code/Spectrum")
MAX_TURNS = 8

# (bulb MACs, Space names) the current call is limited to, or None for every bulb.
_SCOPE = None

SYSTEM = """You control the user's LIFX lights. Turn their description into a lighting look
and apply it with the tools.

- Call list_lights first to see bulbs, groups, rooms and current colors.
- Apply the look with ONE set_lights call covering every bulb that should change. For each
  bulb you leave out, either it keeps its current state or you turn it off explicitly — decide
  on purpose. Turn off bulbs in rooms the description doesn't need.
- Colors: use hex (#rrggbb) for color, or kelvin (2000 warm – 6500 cool) for white. Muted
  looks = lower saturation hex; moody = lower brightness. Brightness is 0..1.
- Default fade_ms 800 so changes feel smooth.
- Only call save_scene if the user asks to save. Never reuse an existing scene name.
- If they name an existing scene or pattern, use recall_scene / play_pattern.
- Finish with one short line describing the look, then a suggested snake_case scene name
  on its own line as: name: <suggested_name>"""

TOOLS = [
    {"name": "list_lights",
     "description": "All bulbs with label, group, room position (x,y 0..1) and current power/color.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "set_lights",
     "description": "Set one or more bulbs. target = bulb label, group name, or 'all'.",
     "input_schema": {"type": "object", "properties": {"changes": {"type": "array", "items": {
         "type": "object",
         "properties": {
             "target": {"type": "string"},
             "power": {"type": "string", "enum": ["on", "off"]},
             "color": {"type": "string", "description": "#rrggbb"},
             "kelvin": {"type": "integer", "minimum": 1500, "maximum": 9000},
             "brightness": {"type": "number", "minimum": 0, "maximum": 1},
             "fade_ms": {"type": "integer", "minimum": 0, "maximum": 600000}},
         "required": ["target"]}}},
         "required": ["changes"]}},
    {"name": "list_scenes", "description": "Names of saved scenes.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "recall_scene", "description": "Apply a saved scene (bulbs not in it turn off).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "save_scene",
     "description": "Save the room's current state as a NEW scene. Fails if the name exists.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "play_pattern", "description": "Play a YAML light pattern (flash/pulse) by name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
]


# ── bulbs ─────────────────────────────────────────────────────────────────────

def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


@contextmanager
def scoped(macs, names=()):
    """Limit every tool (and run/undo/save) to the bulbs with these MACs."""
    global _SCOPE
    prev, _SCOPE = _SCOPE, ({m.lower() for m in macs}, list(names))
    try:
        yield
    finally:
        _SCOPE = prev


def _bulbs():
    cache = _load_json(CACHE_PATH, None)
    if not cache:
        raise RuntimeError(f"no bulb cache at {CACHE_PATH} — run `lite refresh`")
    if _SCOPE is None:
        return cache["lights"]
    return [b for b in cache["lights"] if (b.get("mac") or "").lower() in _SCOPE[0]]


def _light(b):
    from lifxlan import Light
    return Light(b["mac"], b["ip"])


def _resolve(target, bulbs):
    t = target.strip().lower()
    if t == "all":
        return bulbs
    exact = [b for b in bulbs if b["label"].lower() == t]
    return exact or [b for b in bulbs if (b.get("group") or "").lower() == t] \
        or [b for b in bulbs if t in b["label"].lower()]


def _hsbk_to_hex(h, s, b):
    r, g, bl = colorsys.hsv_to_rgb(h / 65535, s / 65535, 1.0)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(bl * 255))


def read_room(bulbs=None):
    """[{label, group, mac, ip, power, hsbk}] — power None if the bulb didn't answer.
    Bulbs are read in parallel, with one retry (UDP replies occasionally drop)."""
    from concurrent.futures import ThreadPoolExecutor

    def one(b):
        rec = dict(b, power=None, hsbk=None)
        for _ in range(2):
            try:
                l = _light(b)
                rec["power"] = "on" if l.get_power() else "off"
                rec["hsbk"] = list(l.get_color())
                break
            except Exception:
                pass
        return rec

    bulbs = bulbs or _bulbs()
    with ThreadPoolExecutor(max_workers=len(bulbs) or 1) as ex:
        return list(ex.map(one, bulbs))


def _write_room(room, fade_ms=600):
    for r in room:
        if r["power"] is None:
            continue
        try:
            l = _light(r)
            l.set_color(tuple(r["hsbk"]), duration=fade_ms, rapid=True)
            l.set_power(r["power"], duration=fade_ms, rapid=True)
        except Exception:
            pass


# ── tools ─────────────────────────────────────────────────────────────────────

def t_list_lights(_):
    layout = _load_json(LAYOUT_PATH, {})
    layout = layout.get("bulbs", layout) if isinstance(layout, dict) else {}
    rows = []
    for r in read_room():
        row = {"label": r["label"], "group": r.get("group"), "power": r["power"] or "unreachable"}
        if r["hsbk"]:
            h, s, b, k = r["hsbk"]
            row.update(brightness=round(b / 65535, 2),
                       color=f"{k}K white" if s < 3000 else _hsbk_to_hex(h, s, b))
        pos = layout.get(r["label"]) if isinstance(layout, dict) else None
        if pos:
            row["pos"] = pos
        rows.append(row)
    return rows


def t_set_lights(args):
    bulbs, done, errors = _bulbs(), [], []
    for c in args["changes"]:
        hits = _resolve(c["target"], bulbs)
        if not hits:
            errors.append(f"no bulb/group matches {c['target']!r}")
            continue
        fade = int(c.get("fade_ms", 800))
        for b in hits:
            try:
                l = _light(b)
                if c.get("power") == "off":
                    l.set_power("off", duration=fade, rapid=True)
                    done.append(f"{b['label']}: off")
                    continue
                bri = c.get("brightness")
                if c.get("color"):
                    hx = c["color"].lstrip("#")
                    h, s, v = colorsys.rgb_to_hsv(*(int(hx[i:i + 2], 16) / 255 for i in (0, 2, 4)))
                    hsbk = (int(h * 65535), int(s * 65535),
                            int((v if bri is None else bri) * 65535), 3500)
                elif c.get("kelvin"):
                    hsbk = (0, 0, int((1.0 if bri is None else bri) * 65535), int(c["kelvin"]))
                elif bri is not None:
                    h, s, _, k = l.get_color()
                    hsbk = (h, s, int(bri * 65535), k)
                else:
                    hsbk = None
                if hsbk:
                    l.set_color(hsbk, duration=fade, rapid=True)
                l.set_power("on", duration=fade, rapid=True)
                done.append(f"{b['label']}: {c.get('color') or (str(c.get('kelvin')) + 'K' if c.get('kelvin') else 'on')}"
                            + (f" @{bri:.2f}" if bri is not None else ""))
            except Exception as e:
                errors.append(f"{b['label']}: {e}")
    return {"applied": done, "errors": errors}


def t_list_scenes(_):
    return sorted(_load_json(SCENES_PATH, {}).get("scenes", {}))


def t_recall_scene(args):
    scene = _load_json(SCENES_PATH, {}).get("scenes", {}).get(args["name"])
    if not scene:
        return {"error": f"no scene {args['name']!r}"}
    from lifxlan import Light
    listed = set()
    in_scope = {b["ip"] for b in _bulbs()}
    for e in scene["lights"]:
        if "ip" not in e:  # non-LIFX (Home Assistant) entries: leave to `lite scene`
            continue
        if _SCOPE is not None and e["ip"] not in in_scope:   # another Space's bulb
            continue
        listed.add(e["ip"])
        l = Light("00:00:00:00:00:00", e["ip"])
        l.set_power("on", duration=800, rapid=True)
        l.set_color((e["h"], e["s"], e["b"], e["k"]), duration=800, rapid=True)
    for b in _bulbs():
        if b["ip"] not in listed:
            _light(b).set_power("off", duration=800, rapid=True)
    return {"recalled": args["name"]}


def scene_lights(name):
    """The LIFX entries of a saved scene: [{label, ip, h, s, b, k}] (empty if unknown)."""
    sc = _load_json(SCENES_PATH, {}).get("scenes", {}).get(name) or {}
    return [e for e in sc.get("lights", []) if "ip" in e]


def t_save_scene(args):
    name = args["name"].strip()
    data = _load_json(SCENES_PATH, None)
    if data is None:
        return {"error": f"can't read {SCENES_PATH}"}
    if name in data.get("scenes", {}):
        return {"error": f"scene {name!r} already exists — pick a new name"}
    lights = [{"label": r["label"], "ip": r["ip"], "h": r["hsbk"][0], "s": r["hsbk"][1],
               "b": r["hsbk"][2], "k": r["hsbk"][3]}
              for r in read_room() if r["power"] == "on"]
    data.setdefault("scenes", {})[name] = {"saved_at": time.time(), "lights": lights}
    with open(SCENES_PATH, "w") as f:
        json.dump(data, f, indent=2)
    return {"saved": name, "lights": len(lights)}


def t_play_pattern(args):
    sys.path.insert(0, SPECTRUM_DIR)
    import patterns as P
    from lifxlan import Light  # noqa: F401  (patterns imports it lazily)

    def to_hsbk(tok, bri):  # minimal: hex only, enough for agent-run patterns
        if tok.startswith("#") and len(tok) == 7:
            h, s, v = colorsys.rgb_to_hsv(*(int(tok[i:i + 2], 16) / 255 for i in (1, 3, 5)))
            return (int(h * 65535), int(s * 65535), int((v if bri is None else bri) * 65535), 3500)
        return None
    try:
        # Prefer lite's full color table when available.
        import importlib.machinery, importlib.util
        loader = importlib.machinery.SourceFileLoader("lite", os.path.expanduser("~/.local/bin/lite"))
        spec = importlib.util.spec_from_loader("lite", loader)
        lite = importlib.util.module_from_spec(spec)
        loader.exec_module(lite)
        to_hsbk = lite.to_hsbk
    except Exception:
        pass
    try:
        p = P.load_pattern(args["name"], to_hsbk)
        cache = {"lights": _bulbs()}
        P.play(p, P.resolve(p, cache, lambda q, c: _resolve(q, c["lights"])))
        return {"played": args["name"]}
    except P.PatternError as e:
        return {"error": str(e)}


HANDLERS = {"list_lights": t_list_lights, "set_lights": t_set_lights,
            "list_scenes": t_list_scenes, "recall_scene": t_recall_scene,
            "save_scene": t_save_scene, "play_pattern": t_play_pattern}


# ── GUI helpers (used by server.py /lights, /scenes) ─────────────────────────

def _kelvin_hex(k):
    """Rough display color for a white-mode bulb."""
    t = max(0.0, min(1.0, (k - 2000) / 4500))
    r, g, b = 255, int(180 + 60 * t), int(110 + 140 * t)
    return "#%02x%02x%02x" % (r, g, b)


def room_view():
    """Per-bulb state for the GUI: label, group, power, hex, brightness, kelvin, white."""
    rows = []
    for r in read_room():
        row = {"label": r["label"], "group": r.get("group") or "Other",
               "power": r["power"] or "unreachable"}
        if r["hsbk"]:
            h, s, b, k = r["hsbk"]
            white = s < 3000
            row.update(brightness=round(b / 65535, 2), kelvin=k, white=white,
                       hex=_kelvin_hex(k) if white else _hsbk_to_hex(h, s, b))
        rows.append(row)
    return rows


def scenes_view():
    """Scenes newest first: [{name, saved_at, colors[]}]."""
    out = []
    for name, sc in _load_json(SCENES_PATH, {}).get("scenes", {}).items():
        cols = [(_kelvin_hex(l["k"]) if l.get("s", 0) < 3000 else _hsbk_to_hex(l["h"], l["s"], l["b"]))
                for l in sc.get("lights", []) if "h" in l]
        out.append({"name": name, "saved_at": sc.get("saved_at", 0), "colors": cols[:6]})
    return sorted(out, key=lambda x: -x["saved_at"])


# ── agent loop ────────────────────────────────────────────────────────────────

def run(prompt: str, log=lambda *a: None) -> dict:
    """Blocking. Returns {reply, suggested_name, actions}. Snapshots room for undo first."""
    key = load_anthropic_key()
    if not key:
        raise RuntimeError("no Anthropic API key — run `lite show setkey`")
    from anthropic import Anthropic
    client = Anthropic(api_key=key, timeout=60)

    os.makedirs(os.path.dirname(UNDO_PATH), exist_ok=True)
    with open(UNDO_PATH, "w") as f:
        json.dump({"saved_at": time.time(), "prompt": prompt, "room": read_room()}, f)

    content = prompt
    if _SCOPE is not None:
        content = (f"[Spaces: {', '.join(_SCOPE[1]) or 'selected'} — only these rooms' bulbs exist for "
                   f"this request; leave every other room alone]\n{prompt}")
    messages = [{"role": "user", "content": content}]
    actions = []
    for _ in range(MAX_TURNS):
        resp = client.messages.create(model=MODEL, max_tokens=2048, system=SYSTEM,
                                      tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            log(f"→ {block.name} {json.dumps(block.input)[:200]}")
            try:
                out = HANDLERS[block.name](block.input)
            except Exception as e:
                out = {"error": str(e)}
            actions.append({"tool": block.name, "input": block.input, "result": out})
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(out)})
        messages.append({"role": "user", "content": results})

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    name = None
    lines = []
    for line in text.splitlines():
        if line.strip().lower().startswith("name:"):
            name = line.split(":", 1)[1].strip() or None
        else:
            lines.append(line)
    return {"reply": "\n".join(lines).strip(), "suggested_name": name, "actions": actions}


def undo() -> dict:
    snap = _load_json(UNDO_PATH, None)
    if not snap:
        return {"error": "nothing to undo"}
    _write_room(snap["room"])
    return {"restored": snap.get("prompt")}


if __name__ == "__main__":
    if sys.argv[1:] == ["--undo"]:
        print(json.dumps(undo()))
    elif sys.argv[1:2] == ["--save"] and len(sys.argv) == 3:
        print(json.dumps(t_save_scene({"name": sys.argv[2]})))
    elif len(sys.argv) > 1:
        r = run(" ".join(sys.argv[1:]), log=lambda m: print(m, file=sys.stderr))
        print(r["reply"])
        if r["suggested_name"]:
            print(f"(save with: lite ask --save {r['suggested_name']})")
    else:
        sys.exit("usage: lights_agent.py \"describe the lights\" | --undo")
