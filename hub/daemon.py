# =============================================================================
# hub/daemon.py — chromad: the existing Engine + SongClock + console + IPC.
#
# Imported by hub/chromad.py AFTER CHROMA_HOME is set (module paths are computed
# at import). `fake=True` swaps the Apple TV for FakeProvider and the bulbs for
# fake LIFX bulbs, so the whole engine runs with no hardware.
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from clock import ClockSample, MonotonicClock, SongClock
from config import Config
from console import ConsoleState
from engine import Engine, LifxController
from fixtures import FixtureStore
from ipc import IpcError, IpcServer
from outputs.lifx import LifxOutput
from outputs.virtual import VirtualOutput
from paths import chroma_dir
from rigsvc import RigService
from state import ART_MODE_IN, StateAssembler

log = logging.getLogger("chroma-hub")

INTENT_NAMES = {"fader", "flash", "assign", "bank", "fader_fn", "hue", "sat", "palette_apply",
                "palette_store", "cue_recall", "cue_store", "blackout", "who"}


class NoLifx(LifxController):
    """`--no-lifx`: discovery finds nothing, so no bulb is ever addressed."""

    def discover(self) -> int:
        self._lights, self._labels = [], []
        return 0


class _NoLan:
    def get_lights(self) -> list:
        return []


def _refuse_light(mac, ip):
    raise OSError("--no-lifx: bulbs are not addressed")


class Chromad:
    POLL_S = 1.0          # SongClock resync from the provider's cached state
    TICK_S = 0.25         # publish the interpolated position at 4 Hz while playing

    def __init__(self, loop, *, fake: bool = False, no_lifx: bool = False, clock=None,
                 socket_path: Optional[str] = None, tracks: Optional[list] = None):
        self.loop = loop
        self.home = chroma_dir()
        self.clock = clock or MonotonicClock()
        self.fake = fake
        self.fake_provider = None
        self.fake_bulbs: list = []
        cfg = Config.load()

        factory = None
        lifx = None
        light_factory = None
        lan_factory = None
        if fake:
            from testing.fake_lifx import (FakeLifxLAN, bedroom_bulbs, light_factory_for,
                                           make_fake_lifx_controller)
            from testing.fake_provider import FakeProvider, default_tracks
            self.fake_provider = FakeProvider(tracks if tracks is not None else default_tracks(),
                                              clock=self.clock)

            async def factory(_loop):
                self.fake_provider.closed = False
                return self.fake_provider
            lifx = NoLifx(cfg) if no_lifx else make_fake_lifx_controller(cfg)
            fake_bulbs = [] if no_lifx else lifx.fake_bulbs + bedroom_bulbs()
            self.fake_bulbs = fake_bulbs
            light_factory = light_factory_for(fake_bulbs)
            lan_factory = lambda: FakeLifxLAN(fake_bulbs)            # noqa: E731
        elif no_lifx:
            lifx = NoLifx(cfg)
            lan_factory = lambda: _NoLan()                            # noqa: E731
            light_factory = _refuse_light

        self.engine = Engine(loop, install_signals=False, provider_factory=factory, lifx=lifx)
        self.song = SongClock(self.clock, on_event=self._on_song_event)
        self.virtual = VirtualOutput(self.clock)
        self.lifx_out = LifxOutput(kelvin=cfg.neutral_kelvin, light_factory=light_factory)
        self.store = FixtureStore.load(os.path.join(self.home, "fixtures.json"))
        self.console = ConsoleState.default(self.store.banks)
        self.rig = RigService(self.engine, self.store, self.lifx_out, self.virtual, loop,
                              notify=self._rig_changed, toast=lambda lv, t: self.ipc.toast(lv, t),
                              lan_factory=lan_factory)
        self.state = StateAssembler(self.engine, self.song, self.console, self.clock, rig=self.rig)
        self.engine.np_listeners.append(self._on_np)
        self.ipc = IpcServer(socket_path or os.path.join(self.home, "chromad.sock"),
                             self.handle, self.state.build)
        self._stop = asyncio.Event()
        self._tasks: list = []
        self._bg: set = set()
        self._who_lock = asyncio.Lock()

    # ---- now playing → SongClock -------------------------------------------
    def _on_np(self, np, source: str) -> None:
        if np is None:
            return
        self.state.latest_np = np
        if np.has_track():
            provider = self.engine.provider
            anchor = None
            if provider is not None and hasattr(provider, "raw_anchor"):
                try:
                    anchor = provider.raw_anchor()
                except Exception:
                    anchor = None
            self.song.update(ClockSample(np.track_id(), bool(np.playing), np.pos_ms, np.dur_ms, anchor))
        self.ipc.mark_dirty()

    def _rig_changed(self) -> None:
        # banks drive the console's channel -> group binding
        for b, row in self.store.banks.items():
            for i, gid in enumerate(row):
                self.console.channel(b, i + 1).group_id = gid
        self.ipc.mark_dirty()

    def _on_song_event(self, event: str, _song) -> None:
        self.state.force_pos()
        self.ipc.mark_dirty()

    # ---- ops ------------------------------------------------------------------
    async def handle(self, req: dict) -> Optional[dict]:
        op = req["op"]
        if op == "get_state":
            return {"state": self.state.build()}
        if op == "refresh":
            res = await self.engine.refresh()
            res.pop("state", None)
            return {"result": res}
        if op == "set_art_mode":
            mode = req.get("mode")
            if mode not in ART_MODE_IN:
                raise IpcError(f"mode must be one of det, paint, ai (got {mode!r})")
            await self.engine.set_mode(ART_MODE_IN[mode])
            return None
        if op == "intent":
            return await self.apply_intent(req.get("intent"), src="ui")
        if op.split(".")[0] in ("fixtures", "groups", "banks"):
            res = await self.rig.handle(op, req)
            if res is not None or op in ("fixtures.test",):
                return res
        raise IpcError(f"unknown op {op!r}")

    async def apply_intent(self, intent, src: str) -> Optional[dict]:
        if not isinstance(intent, dict) or not isinstance(intent.get("intent"), str):
            raise IpcError("intent must be an object with an 'intent' name")
        name = intent["intent"]
        if name not in INTENT_NAMES:
            raise IpcError(f"unknown intent {name!r}")
        if name == "who":
            who = intent.get("who")
            if who not in ("auto", "board"):
                raise IpcError("who must be 'auto' or 'board'")
            self._spawn(self._serialized_who(who))   # never stall the IPC connection
            return None
        raise IpcError(f"intent {name!r} is not available yet (the console arrives in M4)")

    def _spawn(self, coro) -> None:
        t = self.loop.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _serialized_who(self, who: str) -> None:
        async with self._who_lock:                # FIFO: rapid toggles apply in order
            try:
                await self.set_who(who)
            except Exception as e:
                log.warning(f"who={who} failed: {e}")
                self.ipc.toast("error", f"Could not switch to {who.upper()}: {e}")
            self.ipc.mark_dirty()

    async def set_who(self, who: str) -> None:
        """AUTO = the art painter drives (Engine.enabled); BOARD = the console drives.
        Switching to AUTO repaints from cached art (DECISIONS X3)."""
        if who == "auto":
            await self.engine.set_enabled(True)
            if self.engine._last_art is None:
                await self.engine.refresh()
        else:
            await self.engine.set_enabled(False)

    # ---- lifecycle ------------------------------------------------------------
    async def _bus_watch(self) -> None:
        q = self.engine.bus.subscribe()
        try:
            while True:
                ev = await q.get()
                if ev.get("event") == "colors_pushed":
                    self._spawn(self.rig.paint_virtual())
                self.ipc.mark_dirty()
        finally:
            self.engine.bus.unsubscribe(q)

    async def _poll_position(self) -> None:
        while True:
            await asyncio.sleep(self.POLL_S)
            p = self.engine.provider
            if p is not None:
                try:
                    np = await p.now_playing()
                except Exception:
                    np = None
                if np is not None:
                    self._on_np(np, "poll")

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(self.TICK_S)
            if self.fake_provider is not None:
                self.fake_provider.tick()
            if self.song.playing:
                self.ipc.mark_dirty()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        await self.ipc.start()
        engine_task = self.loop.create_task(self.engine.run())
        self._tasks = [engine_task] + [self.loop.create_task(c()) for c in
                                       (self._bus_watch, self._poll_position, self._ticker)]
        log.info(f"chromad running (home {self.home}{', fake devices' if self.fake else ''})")
        if self.store.fresh:          # first start: bring the discovered bulbs into the rig
            self._spawn(self.rig.discover(quiet=True))
        try:
            await self._stop.wait()
        finally:
            await self.shutdown(engine_task)

    async def shutdown(self, engine_task=None) -> None:
        await self.ipc.stop()
        self.engine.stop()
        for t in list(self._tasks) + list(self._bg):
            if t is not engine_task:
                t.cancel()
        if engine_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(engine_task), timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                engine_task.cancel()
        await asyncio.gather(*self._tasks, *self._bg, return_exceptions=True)
        log.info("chromad stopped")
