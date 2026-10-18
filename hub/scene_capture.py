# =============================================================================
# hub/scene_capture.py — capture a scene's real look (lighting-scene-agent-spec §5.2, M2).
#
#   1 record every fixture   2 recall the scene via Driver A   3 settle   4 read back (twice,
#   to see if it moves)   5 store the snapshot   6 restore the original state
#
# `devices` is the bulb side, injected (tests use testing.fake_room.FakeRoomDevices):
#     read()          -> {fixture id: {"power": bool, "hsbk": (h, s, b, k)}}
#     restore(raw)    puts those states back
# `recall_fn(name)` is Driver A (scene_recall.recall with its runner bound). Capture visibly
# cycles the room, so the caller must pass confirmed=True; nothing here asks or prints.
# A scene whose state changes between the two reads is stored static=False: recallable by
# name, never a base for derived scenes.
# =============================================================================

from __future__ import annotations

import time
from typing import Callable, Optional

import scene_normalize as norm
from scene_registry import SceneRegistry, make_snapshot


class CaptureError(RuntimeError):
    pass


def capture_scene(name: str, *, registry: SceneRegistry, room, devices, recall_fn: Callable,
                  settle_s: float = 2.0, recheck_s: float = 1.0, sleep: Callable = time.sleep,
                  confirmed: bool = False, now=None) -> dict:
    """Capture one scene; returns the stored snapshot. Always tries to restore the lights."""
    if confirmed is not True:
        raise CaptureError("capture cycles the lights through the scene; pass confirmed=True")
    if not registry.has(name):
        raise CaptureError(f"scene '{name}' is not in the registry")
    before = devices.read()
    failure, snap = None, None
    try:
        res = recall_fn(name)
        if not res.ok:
            raise CaptureError(f"could not recall '{name}': {res.detail or res.status}")
        sleep(settle_s)
        first = norm.normalize_raw(devices.read())
        sleep(recheck_s)
        second = norm.normalize_raw(devices.read())
        static = not norm.tables_close(first, second)
        snap = make_snapshot(room, name, second, source="capture", static=static, now=now)
        registry.snapshots.save(snap)
    except CaptureError as e:
        failure = e
    except Exception as e:
        failure = CaptureError(f"capture of '{name}' failed: {e}")
    try:
        devices.restore(before)
    except Exception as e:
        raise CaptureError(f"capture of '{name}' left the lights changed; restore failed: {e}") from e
    if failure:
        raise failure
    return snap


def capture_many(names: list, **kw) -> dict:
    """Capture several scenes one after another; a failure is recorded and the batch goes on.
    Returns {name: snapshot | CaptureError}."""
    out = {}
    for n in names:
        try:
            out[n] = capture_scene(n, **kw)
        except CaptureError as e:
            out[n] = e
    return out
