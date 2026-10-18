# =============================================================================
# hub/scene_registry.py — the Scene Agent scene registry (lighting-scene-agent-spec §5, M2).
#
# Read-only knowledge of the scenes that exist:
#   ~/.spectrum_scenes.json   {"scenes": {name: {saved_at, lights: [{label, ip, h, s, b, k}]}}}
#                             written by `lite` and the Agent tab; opened here read-only.
#                             agent.* names are scenes the Agent tab saved; they list apart.
#   registry/scenes.yaml      optional user list (§5.1 source 2): names, each with an optional
#                             note; a name listed here is recallable even if the file lacks it.
#   registry/snapshots/       what scenes look like (contracts/snapshot.schema.json), written by
#                             SnapshotStore only, under Scene Agent's own directory.
#
# A name is only ever passed to a process if it is in the registry AND matches NAME_RE.
# =============================================================================

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import scene_normalize as norm
from room_model import Room

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")     # spec A3; same as scene-spec base.scene
AGENT_PREFIX = "agent."


def default_paths() -> dict:
    """Locations under $CHROMA_HOME / the user's home (computed per call so tests can sandbox)."""
    from paths import chroma_dir
    base = os.path.join(chroma_dir(), "scene-agent", "registry")
    return {"scenes": os.path.expanduser("~/.spectrum_scenes.json"),
            "notes": os.path.join(base, "scenes.yaml"),
            "snapshots": os.path.join(base, "snapshots"),
            "taste": os.path.join(base, "taste_profile.json"),
            "fixtures": os.path.join(chroma_dir(), "fixtures.json")}


def _load_text(path: str):
    with open(path) as f:
        text = f.read()
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        return json.loads(text)


def load_ips(fixtures_path: str) -> dict:
    """fixture id -> ip from fixtures.json (read-only; {} if unreadable)."""
    try:
        with open(fixtures_path) as f:
            fx = json.load(f).get("fixtures", [])
        return {d["id"]: d["ip"] for d in fx if isinstance(d, dict) and d.get("ip") and d.get("id")}
    except (OSError, ValueError, AttributeError, KeyError):
        return {}


@dataclass(frozen=True)
class SceneEntry:
    name: str
    kind: str                       # "lite" | "agent"
    saved_at: float                 # 0 when unknown
    note: str = ""
    lights: int = 0                 # LIFX entries stored in the scenes file
    in_file: bool = True            # False = only on the user's list


class SnapshotStore:
    """registry/snapshots/<scene>.json — the one place Scene Agent writes in M2."""

    def __init__(self, directory: str):
        self.dir = directory

    def _path(self, name: str) -> str:
        if not NAME_RE.match(name):
            raise ValueError(f"bad scene name {name!r}")
        return os.path.join(self.dir, name + ".json")

    def load(self, name: str) -> Optional[dict]:
        try:
            with open(self._path(name)) as f:
                d = json.load(f)
            return d if isinstance(d, dict) and isinstance(d.get("fixtures"), dict) else None
        except (OSError, ValueError):
            return None

    def save(self, snap: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        path = self._path(snap["scene"])
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)

    def names(self) -> list:
        try:
            return sorted(f[:-5] for f in os.listdir(self.dir) if f.endswith(".json") and NAME_RE.match(f[:-5]))
        except OSError:
            return []


def make_snapshot(room: Room, name: str, table: dict, *, source: str, static: bool = True,
                  unmatched=(), now: Optional[datetime] = None) -> dict:
    snap = {"v": 1, "scene": name, "captured_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": source, "static": static, "fixtures": table, "descriptors": norm.describe(room, table)}
    if unmatched:
        snap["unmatched"] = list(unmatched)
    return snap


class SceneRegistry:
    def __init__(self, scenes_path: str, snapshots: SnapshotStore, notes_path: Optional[str] = None):
        self.scenes_path = scenes_path
        self.notes_path = notes_path
        self.snapshots = snapshots
        self.problems: list = []

    @classmethod
    def default(cls) -> "SceneRegistry":
        p = default_paths()
        return cls(p["scenes"], SnapshotStore(p["snapshots"]), p["notes"])

    # ---- reading -----------------------------------------------------------------
    def _file_scenes(self) -> dict:
        try:
            with open(self.scenes_path) as f:
                data = json.load(f)
            scenes = data.get("scenes", {}) if isinstance(data, dict) else {}
            return scenes if isinstance(scenes, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            self.problems.append(f"{self.scenes_path}: {e}")
            return {}

    def _listed(self) -> dict:
        """name -> note from the user's list."""
        if not self.notes_path or not os.path.exists(self.notes_path):
            return {}
        try:
            d = _load_text(self.notes_path) or {}
            s = d.get("scenes", d) if isinstance(d, dict) else d
            if isinstance(s, list):
                return {str(n): "" for n in s}
            return {str(n): "" if v is None else str(v) for n, v in s.items()}
        except Exception as e:      # yaml.YAMLError / bad shape
            self.problems.append(f"{self.notes_path}: {e}")
            return {}

    def entries(self) -> list:
        """Imported (lite) scenes by name, then agent.* scenes by name."""
        self.problems = []
        file_scenes, listed = self._file_scenes(), self._listed()
        out = []
        for name in sorted(set(file_scenes) | set(listed)):
            if not NAME_RE.match(name):
                self.problems.append(f"scene name {name!r} is not usable; skipped")
                continue
            sc = file_scenes.get(name)
            lights = sc.get("lights", []) if isinstance(sc, dict) else []
            out.append(SceneEntry(
                name, "agent" if name.startswith(AGENT_PREFIX) else "lite",
                float(sc.get("saved_at", 0)) if isinstance(sc, dict) and isinstance(sc.get("saved_at", 0), (int, float)) else 0.0,
                listed.get(name, ""), sum(1 for e in lights if isinstance(e, dict) and "ip" in e), sc is not None))
        return sorted(out, key=lambda e: (e.kind == "agent", e.name))

    def names(self) -> list:
        return [e.name for e in self.entries()]

    def has(self, name) -> bool:
        return isinstance(name, str) and bool(NAME_RE.match(name)) and name in self.names()

    def get(self, name: str) -> Optional[SceneEntry]:
        return next((e for e in self.entries() if e.name == name), None)

    def raw_lights(self, name: str) -> Optional[list]:
        sc = self._file_scenes().get(name)
        return sc.get("lights", []) if isinstance(sc, dict) and isinstance(sc.get("lights", []), list) else None

    # ---- snapshots ---------------------------------------------------------------
    def derive_snapshot(self, room: Room, name: str, ips: Optional[dict] = None, now=None) -> Optional[dict]:
        """A snapshot from the lights stored in the scenes file (no bulbs read). None if the scene
        is not stored there."""
        lights = self.raw_lights(name)
        if lights is None:
            return None
        table, unmatched = norm.normalize_lights(room, lights, ips)
        return make_snapshot(room, name, table, source="stored", unmatched=unmatched, now=now)

    def snapshot(self, room: Room, name: str, ips: Optional[dict] = None) -> Optional[dict]:
        """The best snapshot of a scene: captured if there is one, else derived from the file."""
        return self.snapshots.load(name) or self.derive_snapshot(room, name, ips)

    def all_snapshots(self, room: Room, ips: Optional[dict] = None) -> dict:
        return {n: s for n in self.names() if (s := self.snapshot(room, n, ips))}

    def render_snapshots(self, room: Room, ips: Optional[dict] = None) -> dict:
        """{scene: {fixture id: state}} for scene_render.render(): static snapshots only
        (an animated scene is recallable but not a base for derived scenes)."""
        return {n: s["fixtures"] for n, s in self.all_snapshots(room, ips).items() if s.get("static", True)}
