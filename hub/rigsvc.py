# =============================================================================
# hub/rigsvc.py — the rig inside chromad: fixtures/groups/banks ops, discovery,
# the Setup "Test" blink, Room + health for the State, and AUTO colours for
# virtual lights (BUILD_ORDER M3).
#
# The AUTO art painter is untouched: LIFX bulbs are still painted by
# Engine -> LifxController.push_aligned. Virtual fixtures get a deterministic
# art-region colour at their own position (or the paint colours in PAINT mode),
# computed after each paint; AI results are not applied to them.
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from color_pipeline import region_colors
from fixtures import FixtureStore, RigError
from outputs.base import HSB
from outputs.lifx import BulbOff, LifxOutput, Unreachable, discover_bulbs
from outputs.virtual import VirtualOutput

log = logging.getLogger("chroma-hub")


class RigService:
    def __init__(self, engine, store: FixtureStore, lifx: LifxOutput, virtual: VirtualOutput,
                 loop, notify: Callable[[], None], toast: Callable[[str, str], None],
                 lan_factory: Optional[Callable] = None, fake: bool = False):
        self.engine = engine
        self.store = store
        self.lifx = lifx
        self.virtual = virtual
        self.loop = loop
        self.notify = notify
        self.toast = toast
        self.lan_factory = lan_factory
        self.fake = fake
        self._discovering = asyncio.Lock()
        self._blinking: set = set()
        self.live: dict = {}          # BOARD: fixture id -> HSB the console last sent to a LIFX bulb

    # ---- ops ---------------------------------------------------------------------
    async def handle(self, op: str, req: dict) -> Optional[dict]:
        s = self.store
        try:
            if op == "fixtures.list":
                return self.rig_view()
            if op == "fixtures.discover":
                return {"result": await self.discover(), **self.rig_view()}
            if op == "fixtures.upsert":
                before = s.fixtures.get((req.get("fixture") or {}).get("id"))
                old_xy = (before.x, before.y) if before else None
                fx = s.upsert_fixture(req.get("fixture"))
                lbl = self.engine_label(fx) if fx.type == "lifx" else None
                if lbl and old_xy and old_xy != (fx.x, fx.y):
                    # one room: the art painter reads positions from layout.json (keyed by bulb label)
                    await self.engine.set_layout({"lights": {lbl: {"x": fx.x, "y": fx.y}}})
                self.changed()
                return {"fixture": fx.to_dict(), "rev": s.rev}
            if op == "fixtures.remove":
                s.remove_fixture(req.get("fixture_id"))
                self.changed()
                return {"rev": s.rev}
            if op == "fixtures.test":
                await self.test(req.get("fixture_id"))
                return None
            if op == "groups.list":
                return {"groups": [g.to_dict() for g in s.groups.values()], "rev": s.rev}
            if op == "groups.upsert":
                g = s.upsert_group(req.get("group"))
                self.changed()
                return {"group": g.to_dict(), "rev": s.rev}
            if op == "groups.remove":
                s.remove_group(req.get("group_id"))
                self.changed()
                return {"rev": s.rev}
            if op == "spaces.upsert":
                sp = s.upsert_space(req.get("space"))
                self.changed()
                return {"space": sp.to_dict(), "rev": s.rev}
            if op == "spaces.remove":
                s.remove_space(req.get("space_id"))
                self.changed()
                return {"rev": s.rev}
            if op == "banks.get":
                return {"banks": {b: list(r) for b, r in s.banks.items()}, "rev": s.rev}
            if op == "groups.set_limits":
                limits = {k: req[k] for k in ("max_level", "max_flash_hz", "idle_dim_min") if k in req}
                g = s.set_limits(req.get("group_id"), **limits)
                self.changed()
                return {"group": g.to_dict(), "rev": s.rev}
            if op == "banks.swap":
                fa, fb = req.get("from") or {}, req.get("to") or {}
                s.swap_banks((fa.get("bank"), fa.get("channel")), (fb.get("bank"), fb.get("channel")))
                self.changed()
                return {"banks": {b: list(r) for b, r in s.banks.items()}, "rev": s.rev}
            if op == "banks.set":
                s.set_bank(req.get("bank"), req.get("channel"), req.get("group_id"))
                self.changed()
                return {"banks": {b: list(r) for b, r in s.banks.items()}, "rev": s.rev}
        except RigError as e:
            from ipc import IpcError
            raise IpcError(str(e)) from None
        return None

    def changed(self) -> None:
        self.notify()

    def rig_view(self) -> dict:
        return {**self.store.to_dict(), "online": self.online()}

    # ---- discovery ------------------------------------------------------------------
    async def discover(self, quiet: bool = False) -> dict:
        async with self._discovering:
            try:
                bulbs = await self.loop.run_in_executor(None, discover_bulbs, self.lan_factory)
            except Exception as e:
                if not quiet:
                    self.toast("error", f"LIFX discovery failed: {e}")
                return {"found": 0, "added": [], "updated": [], "error": str(e)}
            summary = self.store.import_discovered(bulbs, positions=self.engine.layout.pos_for)
            self.changed()
            if not quiet:
                n = len(summary["added"])
                self.toast("info", f"Found {summary['found']} LIFX bulb(s)" + (f", added {n}" if n else ""))
            return summary

    # ---- Test (identify a fixture) ------------------------------------------------------
    async def test(self, fid) -> None:
        fx = self.store.fixtures.get(fid)
        if fx is None:
            raise RigError(f"no fixture {fid!r}")
        if fx.status != "active":
            raise RigError(f"{fx.label} is {fx.status.replace('_', ' ')} — nothing to blink")
        if fx.type == "lifx":
            if fx.id in self._blinking:              # a double-click: the first pulse is still running
                return
            self._blinking.add(fx.id)
            try:
                await self.loop.run_in_executor(None, self.lifx.blink, fx)
            except (BulbOff, Unreachable) as e:
                raise RigError(str(e)) from None
            except Exception as e:
                log.warning(f"Test {fx.label} failed: {e!r}")
                raise RigError(f"could not reach {fx.label}") from None
            finally:
                self._blinking.discard(fx.id)
            # a fake engine (dev builds) blinks a stand-in: say so, or a still room looks like a bug
            self.toast("info", f"Blinked {fx.label}" + (" (fake bulb — no real light changes)" if self.fake else ""))
        elif fx.type == "virtual":
            self.loop.create_task(self._blink_virtual(fx))
        else:
            raise RigError(f"{fx.label}: WLED output arrives in phase 2")

    async def _blink_virtual(self, fx) -> None:
        t0 = 0.0
        for delay, hsb in self.virtual.blink(fx):
            await asyncio.sleep(max(0.0, delay - t0))
            t0 = delay
            self.virtual.push(fx, hsb, 0)
            self.notify()

    # ---- AUTO colours for virtual fixtures ---------------------------------------------
    async def paint_virtual(self) -> None:
        eng = self.engine
        vfx = self.store.active("virtual")
        if not vfx or not eng.enabled:
            return
        coords = [(f.x, f.y) for f in vfx]
        params = eng.cfg.pipeline_params()
        try:
            if eng.cfg.mode == "paint" and eng.cfg.paint_colors:
                rgb = eng._paint_rgb(coords)
            elif eng._last_art is not None:
                rgb = await self.loop.run_in_executor(None, region_colors, eng._last_art, coords,
                                                      eng.layout, params)
            else:
                return
        except Exception as e:
            log.warning(f"virtual paint failed: {e}")
            return
        for f, c in zip(vfx, rgb):
            self.virtual.push(f, HSB.from_rgb(c, b=params.brightness), 0)
        self.notify()

    # ---- State pieces -------------------------------------------------------------------
    def _engine_bulbs(self) -> dict:
        """mac -> label of the bulbs the engine discovered (lifxlan keeps both locally)."""
        out = {}
        for light, label in zip(getattr(self.engine.lifx, "_lights", []), self.engine.lifx.labels()):
            try:
                out[str(light.get_mac_addr()).lower()] = label
            except Exception:
                pass
        return out

    def engine_label(self, f, bulbs: Optional[dict] = None) -> Optional[str]:
        """The engine's label for a LIFX fixture: by MAC, or by name for IP-only fixtures."""
        bulbs = self._engine_bulbs() if bulbs is None else bulbs
        if f.mac:
            return bulbs.get(f.mac)
        return f.label if f.label in bulbs.values() else None

    def online(self) -> dict:
        bulbs = self._engine_bulbs()
        out = {}
        for f in self.store.fixtures.values():
            if f.status != "active":
                out[f.id] = False
            elif f.type == "lifx":
                out[f.id] = self.engine_label(f, bulbs) is not None
            else:
                out[f.id] = f.type == "virtual"
        return out

    def room(self) -> list:
        per_light = {p["label"]: p.get("hex") for p in (self.engine._last_per_light or [])}
        bulbs = self._engine_bulbs()
        out = []
        for f in self.store.active():
            if f.type == "lifx":
                lbl = self.engine_label(f, bulbs)
                hexc, online = per_light.get(lbl) if lbl else None, lbl is not None
                if not self.engine.enabled and f.id in self.live:      # BOARD: what the console sent
                    hexc = self.live[f.id].hex()
            elif f.type == "virtual":
                hexc, online = self.virtual.hex_for(f.id), True
            else:
                continue
            out.append({"fixture_id": f.id, "label": f.label, "x": round(f.x, 3), "y": round(f.y, 3),
                        "hex": hexc, "online": online})
        return out

    def lifx_health(self) -> dict:
        active = self.store.active("lifx")
        bulbs = self._engine_bulbs()
        return {"found": sum(1 for f in active if self.engine_label(f, bulbs) is not None), "total": len(active)}
