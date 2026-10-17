# =============================================================================
# hub/agentsvc.py — the Agent tab inside chromad: agent.ask / agent.undo /
# agent.save over IPC, wrapping hub/lights_agent.py (the same agent behind the
# hub's POST /agent and the ⌃⇧L prompt).
#
# A run takes seconds to minutes, longer than the app's 30 s request timeout, so
# agent.ask only starts it: progress and the result reach the UI as State.agent
# patches. One run at a time. Before anything touches the bulbs, AUTO painting
# stops (WHO DRIVES shows BOARD), like /agent, so the look isn't painted over.
#
# lights_agent addresses the real bulbs from ~/.lifx_cli_cache.json whatever the
# engine's Devices mode is, so every agent op is refused on a fake engine.
# Saves are create-only lite scenes, always named agent.<name>.
#
# agent.ask names the Spaces (rooms) the agent may touch — none = living-room. The
# run, its Undo, Save and the look read back are all limited to the LIFX bulbs in
# those Spaces (lights_agent.scoped), so a Bedroom scene leaves the living room be.
# =============================================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from fixtures import DEFAULT_SPACE
from ipc import IpcError

log = logging.getLogger("chroma-hub")

MAX_PROMPT = 500
NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
MAX_ACTIONS = 50
MAX_LOOK = 64


FAKE_BLOCK = "the agent drives your real bulbs — switch Devices → Real to use it"


def idle_state(blocked: Optional[str] = None) -> dict:
    return {"status": "idle", "blocked": blocked, "prompt": "", "reply": "", "suggested_name": None, "actions": [],
            "look": [], "error": None, "can_undo": False, "saved_as": None}


def _clip(s: str, n: int = 240) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def summarize(action: dict) -> dict:
    """One readable line per tool call the agent made."""
    tool, inp, out = action.get("tool", "?"), action.get("input") or {}, action.get("result")
    if isinstance(out, dict) and out.get("error"):
        text = f"failed: {out['error']}"
    elif tool == "set_lights" and isinstance(out, dict):
        parts = list(out.get("applied", [])) + [f"✗ {e}" for e in out.get("errors", [])]
        text = ", ".join(parts) or "no bulbs changed"
    elif tool == "list_lights" and isinstance(out, list):
        text = f"read {len(out)} bulbs"
    elif tool == "list_scenes" and isinstance(out, list):
        text = f"looked at {len(out)} saved scenes"
    elif tool == "recall_scene":
        text = f"recalled scene {inp.get('name', '')}"
    elif tool == "play_pattern":
        text = f"played pattern {inp.get('name', '')}"
    elif tool == "save_scene":
        text = f"saved scene {inp.get('name', '')}"
    else:
        text = json.dumps(inp)
    return {"tool": tool, "summary": _clip(text)}


def suggested(name) -> Optional[str]:
    if not isinstance(name, str):
        return None
    s = re.sub(r"[^a-z0-9_-]+", "_", name.strip().lower()).strip("_-")[:32]
    return s if NAME_RE.match(s) else None


def look_rows(rows: list) -> list:
    out = []
    for r in rows[:MAX_LOOK]:
        power = r.get("power")
        b = r.get("brightness")
        out.append({"label": str(r.get("label", "")),
                    "power": power if power in ("on", "off") else "unreachable",
                    "hex": r.get("hex"),
                    "brightness": None if b is None else round(min(1.0, max(0.0, float(b))), 3)})
    return out


class AgentService:
    def __init__(self, d):
        self.d = d                       # Chromad: engine, ipc, fake, spawn, _who_lock
        self.s = idle_state()
        self._busy = False               # an undo / save is in flight
        self._scope = None               # (MACs, Space names) of the last ask

    def state_fields(self) -> dict:
        return {"agent": {**self.s, "blocked": FAKE_BLOCK if self.d.fake else None, "actions": list(self.s["actions"]), "look": list(self.s["look"])}}

    def _set(self, **kw) -> None:
        self.s = {**self.s, **kw}
        self.d.ipc.mark_dirty()

    async def handle(self, op: str, req: dict) -> Optional[dict]:
        if op not in ("agent.ask", "agent.undo", "agent.save"):
            raise IpcError(f"unknown op {op!r}")
        if self.d.fake:
            raise IpcError(FAKE_BLOCK)
        if self.s["status"] == "running" or self._busy:
            raise IpcError("the agent is still working on the last request")
        if op == "agent.ask":
            return await self.ask(req.get("prompt"), req.get("spaces"))
        self._busy = True
        try:
            return await (self.undo() if op == "agent.undo" else self.save(req.get("name")))
        finally:
            self._busy = False

    async def handle_scenes(self, op: str, req: dict) -> Optional[dict]:
        """scenes.list / scenes.recall: the saved lite scenes (Console Cues pane's Scenes mode)."""
        import lights_agent
        if op not in ("scenes.list", "scenes.recall"):
            raise IpcError(f"unknown op {op!r}")
        if self.d.fake:
            raise IpcError(FAKE_BLOCK)
        loop = asyncio.get_running_loop()
        if op == "scenes.list":
            return {"scenes": await loop.run_in_executor(None, lights_agent.scenes_view)}
        name = req.get("name")
        if not isinstance(name, str) or not name or len(name) > 64:
            raise IpcError("name: 1-64 characters")
        await self._stop_painting()
        res = await loop.run_in_executor(None, lights_agent.t_recall_scene, {"name": name})
        if isinstance(res, dict) and res.get("error"):
            raise IpcError(res["error"])
        entries = await loop.run_in_executor(None, lights_agent.scene_lights, name)
        self.d.adopt_look(self._scene_looks(entries))
        return None

    def _scene_looks(self, entries: list) -> dict:
        """fixture id -> HSB the recalled scene left each active LIFX bulb in (off if not in it)."""
        from outputs.base import HSB
        looks = {}
        for f in self.d.store.active("lifx"):
            e = next((e for e in entries if (f.ip and e.get("ip") == f.ip) or e.get("label") == f.label), None)
            if e is not None:
                looks[f.id] = HSB(e["h"] / 65535.0 * 360.0, e["s"] / 65535.0, e["b"] / 65535.0)
            else:
                prev = self.d.console.art.get(f.id)
                looks[f.id] = HSB(prev.h if prev else 0.0, prev.s if prev else 0.0, 0.0)
        return looks

    async def _stop_painting(self) -> None:
        """Like POST /agent: AUTO stops painting so the look stays (WHO DRIVES → BOARD)."""
        eng = self.d.engine
        if not eng.enabled:
            return
        async with self.d._who_lock:
            if eng.enabled:
                await eng.set_enabled(False)
        self.d.ipc.toast("info", "Switched to BOARD for the agent")
        self.d.ipc.mark_dirty()

    def resolve_spaces(self, spaces) -> tuple:
        """Space ids -> (MACs of their LIFX bulbs, Space names). None / [] = living-room."""
        if spaces is None:
            spaces = []
        if not isinstance(spaces, list) or not all(isinstance(x, str) for x in spaces):
            raise IpcError("spaces must be a list of space ids")
        store = self.d.store
        ids = list(dict.fromkeys(spaces)) or [DEFAULT_SPACE]
        unknown = [x for x in ids if x not in store.spaces]
        if unknown:
            raise IpcError(f"no space {unknown[0]!r}")
        names = [store.spaces[x].name for x in ids]
        macs = {f.mac for f in store.in_spaces(ids) if f.type == "lifx" and f.mac}
        if not macs:
            raise IpcError(f"there are no LIFX bulbs in {' / '.join(names)} — assign some in the Room panel's Spaces")
        return macs, names

    async def _scoped(self, fn, *args):
        """Run a blocking lights_agent call in the executor, limited to the last ask's Spaces."""
        import lights_agent
        scope = self._scope

        def call():
            if scope is None:
                return fn(*args)
            with lights_agent.scoped(*scope):
                return fn(*args)
        return await asyncio.get_running_loop().run_in_executor(None, call)

    async def _read_look(self) -> list:
        import lights_agent
        try:
            return look_rows(await self._scoped(lights_agent.room_view))
        except Exception as e:
            log.warning(f"agent: could not read the bulbs back: {e}")
            return []

    async def ask(self, prompt, spaces=None) -> Optional[dict]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise IpcError("say what the room should look like")
        prompt = prompt.strip()
        if len(prompt) > MAX_PROMPT:
            raise IpcError(f"keep it under {MAX_PROMPT} characters")
        self._scope = self.resolve_spaces(spaces)
        self._set(**{**idle_state(), "status": "running", "prompt": prompt})
        self.d.spawn(self._run(prompt))
        return None

    async def _run(self, prompt: str) -> None:
        import lights_agent
        try:
            await self._stop_painting()
            res = await self._scoped(lights_agent.run, prompt)
        except Exception as e:
            log.warning(f"agent run failed: {e}")
            self._set(status="error", error=str(e) or type(e).__name__, look=await self._read_look())
            return
        actions = [summarize(a) for a in (res.get("actions") or [])][:MAX_ACTIONS]
        self._set(status="done", reply=res.get("reply") or "", suggested_name=suggested(res.get("suggested_name")),
                  actions=actions, can_undo=True, look=await self._read_look())

    async def undo(self) -> Optional[dict]:
        import lights_agent
        if not self.s["can_undo"]:
            raise IpcError("nothing to undo")
        await self._stop_painting()
        res = await self._scoped(lights_agent.undo)
        if isinstance(res, dict) and res.get("error"):
            raise IpcError(res["error"])
        self._set(can_undo=False, look=await self._read_look())
        return None

    async def save(self, name) -> Optional[dict]:
        import lights_agent
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise IpcError("name: 1-32 of a-z, 0-9, _ or -")
        full = f"agent.{name}"
        res = await self._scoped(lights_agent.t_save_scene, {"name": full})
        if isinstance(res, dict) and res.get("error"):
            raise IpcError(res["error"])
        self._set(saved_as=full)
        return {"saved": full}
