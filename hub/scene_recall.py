# =============================================================================
# hub/scene_recall.py — Output Driver A, `lite` passthrough (lighting-scene-agent-spec §7.3, M2).
#
#   recall(name, registry) runs `lite scene <name>` as an argument vector (no shell), but only
#   for a name that is in the registry and matches its pattern; anything else is refused before
#   any process is started. Because A2 (exit status) is unverified, a recall can be read back:
#   with a `reader` and an expected snapshot the lights are compared after a settle delay and a
#   mismatch is reported rather than assumed away.
#
# The process runner, `which`, sleep and reader are injected; tests never start `lite`.
# The existing recall paths (lights_agent.t_recall_scene, POST /scenes/recall) are untouched.
# =============================================================================

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import scene_normalize as norm
from scene_registry import NAME_RE, SceneRegistry

LITE_FALLBACKS = ("~/.local/bin/lite",)     # where `lite` is installed (lite_cli_session.md)


@dataclass
class RecallResult:
    ok: bool
    name: str
    status: str                     # applied | failed | refused | unavailable
    verified: str = "skipped"       # verified | mismatch | skipped
    detail: str = ""
    argv: list = field(default_factory=list)
    mismatches: list = field(default_factory=list)


def find_lite(lite: Optional[str] = None, which: Callable = shutil.which) -> Optional[str]:
    """The lite executable: an explicit path, `lite` on PATH, or the install location."""
    for cand in ([lite] if lite else [which("lite"), *LITE_FALLBACKS]):
        if cand:
            p = os.path.expanduser(cand)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return None


def recall(name, registry: SceneRegistry, *, run: Callable = subprocess.run, lite: Optional[str] = None,
           which: Callable = shutil.which, timeout: float = 30.0, reader=None, room=None, ips=None,
           settle_s: float = 1.5, sleep: Callable = time.sleep) -> RecallResult:
    if not isinstance(name, str) or not NAME_RE.match(name):
        return RecallResult(False, str(name)[:64], "refused", detail="not a valid scene name")
    if not registry.has(name):
        return RecallResult(False, name, "refused", detail=f"scene '{name}' is not in the registry")
    exe = find_lite(lite, which)
    if exe is None:
        looked = lite or "'lite' on PATH, " + ", ".join(LITE_FALLBACKS)
        return RecallResult(False, name, "unavailable", detail=f"lite executable not found (looked for {looked})")
    argv = [exe, "scene", name]
    try:
        p = run(argv, capture_output=True, text=True, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired:
        return RecallResult(False, name, "failed", detail=f"lite timed out after {timeout:g}s", argv=argv)
    except OSError as e:
        return RecallResult(False, name, "failed", detail=f"could not run lite: {e}", argv=argv)
    res = RecallResult(p.returncode == 0, name, "applied" if p.returncode == 0 else "failed", argv=argv)
    if p.returncode != 0:
        res.detail = ((p.stderr or p.stdout or "").strip().splitlines() or [f"lite exited {p.returncode}"])[-1]
    if reader is not None and room is not None:
        _verify(res, registry, reader, room, ips, settle_s, sleep)
    return res


def _verify(res: RecallResult, registry, reader, room, ips, settle_s, sleep) -> None:
    expected = registry.snapshot(room, res.name, ips)
    if not expected or not expected.get("static", True):
        return
    sleep(settle_s)
    try:
        got = norm.normalize_raw(reader.read())
    except Exception as e:      # an unreachable bulb must not turn a recall into a crash
        res.detail = (res.detail + "; " if res.detail else "") + f"read-back failed: {e}"
        return
    res.mismatches = norm.tables_close(expected["fixtures"], got)
    res.verified = "mismatch" if res.mismatches else "verified"
    if res.mismatches:
        res.ok = False
        res.detail = (res.detail + "; " if res.detail else "") + f"lights differ from the scene: {', '.join(res.mismatches)}"
