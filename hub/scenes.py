# =============================================================================
# hub/scenes.py  —  Pass 4.5
# Persistent per-album "scenes": remember the exact lighting for an album so it
# is reproduced automatically whenever that album plays again — no re-run.
# We only save meaningful work: an AI-enhanced result, or a custom paint scene.
# Deterministic output is cheap and not cached.
#
# Persisted to ~/.chroma/scenes.json, keyed by "artist|||album" (or track id).
# Scene shapes:
#   {"type":"paint", "distribution": "...", "colors":[{"hex","x","y"}], ...}
#   {"type":"ai",    "rgb":[[r,g,b]...], "layout_fp":"...",
#                    "mood":"...", "palette":[...], "reasoning":"..."}
# =============================================================================

import json
import os
from datetime import datetime, timezone

SCENES_PATH = os.path.expanduser("~/.chroma/scenes.json")


class SceneStore:
    def __init__(self, data=None):
        self.data = data or {}

    @classmethod
    def load(cls) -> "SceneStore":
        if os.path.exists(SCENES_PATH):
            try:
                with open(SCENES_PATH) as f:
                    return cls(json.load(f))
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        os.makedirs(os.path.dirname(SCENES_PATH), exist_ok=True)
        tmp = SCENES_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, SCENES_PATH)

    def get(self, key):
        return self.data.get(key) if key else None

    def has(self, key) -> bool:
        return bool(key) and key in self.data

    def set(self, key, scene: dict) -> None:
        if not key:
            return
        scene = dict(scene)
        scene["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.data[key] = scene
        self.save()

    def clear(self, key) -> bool:
        if key and key in self.data:
            del self.data[key]
            self.save()
            return True
        return False
