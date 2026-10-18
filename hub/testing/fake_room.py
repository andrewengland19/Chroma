# =============================================================================
# hub/testing/fake_room.py — Scene Agent test doubles (no bulbs, no `lite`).
#
# FakeRoomDevices implements the bulb side capture/recall verification use:
#   read() -> {fixture id: {"power", "hsbk"}}   restore(raw)
# fake_lite(devices, scenes) is a stub `lite`: a subprocess.run stand-in that records its argv
# and, for `lite scene <name>`, sets the devices to that scene (lights it does not list go off,
# as a real recall does). Scenes named in `animated` keep drifting on every read.
# =============================================================================

from __future__ import annotations

import copy
import subprocess
from typing import Optional


class FakeRoomDevices:
    def __init__(self, ids: list, hsbk=(0, 0, 32768, 3500)):
        self.state = {i: {"power": True, "hsbk": tuple(hsbk)} for i in ids}
        self.drift = 0.0
        self.reads = 0
        self.restored: list = []
        self.fail_restore = False

    def read(self) -> dict:
        self.reads += 1
        if self.drift:
            for d in self.state.values():
                h, s, b, k = d["hsbk"]
                d["hsbk"] = (h, s, int(min(65535, (b + self.drift * 65535) % 65536)), k)
        return copy.deepcopy(self.state)

    def restore(self, raw: dict) -> None:
        if self.fail_restore:
            raise RuntimeError("bulb unreachable")
        self.restored.append(copy.deepcopy(raw))
        self.state = copy.deepcopy(raw)
        self.drift = 0.0


def fake_lite(devices: FakeRoomDevices, scenes: dict, animated=(), returncode: Optional[int] = None):
    """A subprocess.run replacement. scenes: {name: {fixture id: (h, s, b, k)}}. Returns the
    runner; `runner.calls` holds every argv it was given."""
    def run(argv, **kw):
        run.calls.append(list(argv))
        assert kw.get("shell") in (None, False), "lite must never run through a shell"
        name = argv[-1]
        if returncode is not None:
            return subprocess.CompletedProcess(argv, returncode, "", "boom" if returncode else "")
        if argv[1:2] != ["scene"] or name not in scenes:
            return subprocess.CompletedProcess(argv, 1, "", f"unknown scene {name}")
        for fid in devices.state:
            hsbk = scenes[name].get(fid)
            devices.state[fid] = {"power": hsbk is not None, "hsbk": tuple(hsbk) if hsbk else (0, 0, 0, 2700)}
        devices.drift = 0.05 if name in animated else 0.0
        return subprocess.CompletedProcess(argv, 0, "", "")
    run.calls = []
    return run
