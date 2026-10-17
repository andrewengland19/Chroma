# =============================================================================
# hub/daemon.py — chromad: the existing Engine + SongClock + console + IPC.
#
# Imported by hub/chromad.py AFTER CHROMA_HOME is set (module paths are computed
# at import). `fake=True` swaps the Apple TV for FakeProvider and the bulbs for
# fake LIFX bulbs, so the whole engine runs with no hardware.
# =============================================================================

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from clock import ClockSample, MonotonicClock, SongClock
from agentsvc import AgentService
from config import Config
import console as console_logic
from console import ConsoleError, ConsoleState
from engine import Engine, LifxController
from fixtures import FixtureStore
from ipc import IpcError, IpcServer
from outputs.base import HSB
from outputs.lifx import LifxOutput
from outputs.virtual import VirtualOutput
from paths import chroma_dir
from rigsvc import RigService
from scheduler import OutputScheduler
from state import ART_MODE_IN, StateAssembler
from takes import TakeError, TakeService, resolve_event

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
                 socket_path: Optional[str] = None, tracks: Optional[list] = None, replay_offset_ms: int = 0):
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
        self.console_path = os.path.join(self.home, "console.json")
        self.console = self._load_console()
        self.rig = RigService(self.engine, self.store, self.lifx_out, self.virtual, loop,
                              notify=self._rig_changed, toast=lambda lv, t: self.ipc.toast(lv, t),
                              lan_factory=lan_factory, fake=fake)
        self.state = StateAssembler(self.engine, self.song, self.console, self.clock, rig=self.rig)
        self.engine.np_listeners.append(self._on_np)
        self.ipc = IpcServer(socket_path or os.path.join(self.home, "chromad.sock"),
                             self.handle, self.state.build)
        self._stop = asyncio.Event()
        self._tasks: list = []
        self._bg: set = set()
        self._who_lock = asyncio.Lock()
        # BOARD: the console drives fixtures through the scheduler (§5.3)
        self.scheduler = OutputScheduler(lambda: self.clock.monotonic() * 1000.0, self._push,
                                         self._klass, self.store.limits)
        self._board_ready = False        # seeded from the art look since BOARD began (D2)
        self._save_task: Optional[asyncio.Task] = None
        # M5: recorder + replay (takes.py); State rec/play/activity/takes_rev come from it
        self.takes = TakeService(self, os.path.join(self.home, "takes"), offset_ms=replay_offset_ms)
        self.state.takes = self.takes
        self.agent = AgentService(self)
        self.state.agent = self.agent

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
        if self._board_ready and not self.engine.enabled:
            self._drive(self.clock.monotonic() * 1000.0)     # membership / caps changed
        self.ipc.mark_dirty()

    def _on_song_event(self, event: str, _song) -> None:
        self.state.force_pos()
        self.takes.on_song_event(event)
        self.ipc.mark_dirty()

    def poll_now(self) -> None:
        """Feed the fake provider's position into the SongClock right away (after a seek)."""
        if self.fake_provider is not None:
            from providers import NowPlaying
            self._on_np(NowPlaying.from_playing(self.fake_provider.playing()), "seek")

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
        if op == "cues.rename":
            i, name = req.get("i"), req.get("name")
            if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i <= 7:
                raise IpcError("i must be 0-7")
            if not isinstance(name, str) or not name.strip() or len(name) > 24:
                raise IpcError("name must be 1-24 characters")
            self.console.cues[i].name = name.strip()
            self._console_changed()
            return None
        if op.split(".")[0] in ("takes", "take", "rec", "play"):
            try:
                return await self.takes.handle(op, req)
            except TakeError as e:
                raise IpcError(str(e)) from None
        if op.split(".")[0] == "agent":
            return await self.agent.handle(op, req)
        if op.split(".")[0] in ("fixtures", "groups", "banks"):
            res = await self.rig.handle(op, req)
            if res is not None or op in ("fixtures.test",):
                return res
        raise IpcError(f"unknown op {op!r}")

    async def apply_intent(self, intent, src: str, at: Optional[dict] = None) -> Optional[dict]:
        """Every console change enters here (src ui | board | replay | cli). `at` (replay):
        the take event whose resolved b/c/a/v is applied (D9)."""
        if not isinstance(intent, dict) or not isinstance(intent.get("intent"), str):
            raise IpcError("intent must be an object with an 'intent' name")
        name = intent["intent"]
        if name not in INTENT_NAMES:
            raise IpcError(f"unknown intent {name!r}")
        if name == "who":
            who = intent.get("who")
            if who not in ("auto", "board"):
                raise IpcError("who must be 'auto' or 'board'")
            # Who drives changes now, so intents after this one on the connection see it;
            # the slower part (config save, repaint) runs in the background, FIFO.
            if who == "board":
                self.engine.enabled = False
                self._ensure_board()
            else:
                self._leave_board()
                self.engine.enabled = True
            self._spawn(self._serialized_who(who))   # never stall the IPC connection
            return None
        t_intent = self.clock.monotonic() * 1000.0
        gid = intent.get("group_id") if name == "assign" else None
        if gid is not None and gid not in self.store.groups:
            raise IpcError(f"no group {gid!r}")
        board = not self.engine.enabled
        if board:
            self._ensure_board()                    # before apply: the seed must not undo this move
        elif self._board_ready:
            self._leave_board()                     # re-enabled elsewhere (/agent, /lights/set)
        try:
            effects = console_logic.apply(self.console, intent, src, at=at)
        except ConsoleError as e:
            raise IpcError(str(e)) from None
        if src != "replay" and self.takes.rec is not None:
            resolved = resolve_event(self.console, intent, src)
            if resolved is not None:
                self.takes.record(intent, resolved)
        fade = 0
        for eff in effects:
            if eff[0] == "assign":
                self.store.set_bank(eff[1], eff[2], eff[3])   # persists; _rig_changed re-binds
                self.rig.changed()
            elif eff[0] == "recall":
                fade = eff[2]
        if board:
            self._drive(t_intent, fade)
        self._console_changed()
        return None

    # ---- BOARD: console -> scheduler -> outputs ----------------------------------
    def _members(self) -> dict:
        return {gid: [f.id for f in self.store.members_of(gid) if f.type in ("lifx", "virtual")]
                for gid in self.store.groups}

    def _klass(self, fid: str) -> str:
        f = self.store.fixtures.get(fid)
        return "slow" if f is None or f.type == "lifx" else "fast"

    def _push(self, fid: str, hsb: HSB, fade_ms: int) -> None:
        if self.engine.enabled:                     # AUTO: the art painter owns the fixtures
            return
        f = self.store.fixtures.get(fid)
        if f is None or f.status != "active":
            return
        if f.type == "lifx":
            self.lifx_out.push(f, hsb, fade_ms)
            self.rig.live[fid] = hsb
        elif f.type == "virtual":
            self.virtual.push(f, hsb, fade_ms)
        self.ipc.mark_dirty()

    def drive_now(self) -> None:
        if not self.engine.enabled:
            self._drive(self.clock.monotonic() * 1000.0)
        self._console_changed()

    def apply_replay(self, ev: dict, drive: bool = True) -> None:
        """One take event (replay): the same console.apply path as live input, src="replay".
        Synchronous: the replay loop applies a batch, then drives once."""
        intent = ev.get("i") or {"intent": "replay"}
        t_intent = self.clock.monotonic() * 1000.0
        effects = console_logic.apply(self.console, intent, "replay", at=ev if ev.get("a") else None)
        fade = next((e[2] for e in effects if e[0] == "recall"), 0)
        if drive and not self.engine.enabled:
            self._drive(t_intent, fade)

    def ensure_board(self, why: str) -> None:
        """REC / PLAY drive the lights from the console: switch to BOARD if AUTO paints."""
        if self.engine.enabled:
            self.engine.enabled = False
            self._ensure_board()
            self._spawn(self._serialized_who("board"))
            self.ipc.toast("info", f"Switched to BOARD for {why}")

    def spawn(self, coro) -> None:
        self._spawn(coro)

    def _drive(self, t_intent_ms: float, fade_ms: int = 0) -> None:
        targets = console_logic.resolve(self.console, self._members(), self.store.limits)
        self.scheduler.submit(targets, t_intent_ms, fade_ms, dark=self.console.blackout)

    def art_look(self) -> dict:
        """fixture id -> HSB the room shows from the art painter right now."""
        eng = self.engine
        by_label = {}
        for p, (h, s_, b, _k) in zip(eng._last_per_light or [], eng._last_hsbk or []):
            by_label[p["label"]] = HSB(h / 65535.0 * 360.0, s_ / 65535.0, b / 65535.0)
        out = {}
        bulbs = self.rig._engine_bulbs()
        for f in self.store.active("lifx"):
            lbl = self.rig.engine_label(f, bulbs)
            if lbl in by_label:
                out[f.id] = by_label[lbl]
        for f in self.store.active("virtual"):
            if f.id in self.virtual.current:
                out[f.id] = self.virtual.current[f.id]
        return out

    def _ensure_board(self) -> None:
        """D2: BOARD starts from the art look — channels are seeded from it and nothing is
        pushed until the console changes something."""
        if self._board_ready:
            return
        art = self.art_look()
        console_logic.seed_from_art(self.console, art, self._members())
        self.scheduler.reset()
        self.scheduler.prime(art)
        self.rig.live = dict((k, v) for k, v in art.items() if self._klass(k) == "slow")
        self.lifx_out.forget_power()        # bulbs may have been switched off elsewhere
        self._board_ready = True

    def _leave_board(self) -> None:
        self._board_ready = False
        self.scheduler.reset()
        self.rig.live.clear()

    # ---- console.json -----------------------------------------------------------
    def _load_console(self) -> ConsoleState:
        try:
            with open(self.console_path) as fh:
                return console_logic.from_file(json.load(fh), self.store.banks)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"console.json unreadable ({e}); starting from defaults")
            try:
                os.replace(self.console_path, self.console_path + ".bad")
            except OSError:
                pass
        return ConsoleState.default(self.store.banks)

    def save_console(self) -> None:
        tmp = self.console_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(console_logic.to_file(self.console), fh, indent=1)
        os.replace(tmp, self.console_path)

    def _console_changed(self) -> None:
        self.ipc.mark_dirty()
        if self._save_task is None or self._save_task.done():
            self._save_task = self.loop.create_task(self._save_soon())

    async def _save_soon(self) -> None:
        await asyncio.sleep(0.5)                    # coalesce a fader drag into one write
        try:
            self.save_console()
        except OSError as e:
            log.warning(f"could not save console.json: {e}")

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
            self._leave_board()
            await self.engine.set_enabled(True)
            if self.engine._last_art is None:
                await self.engine.refresh()
        else:
            await self.engine.set_enabled(False)
            self._ensure_board()

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
                                       (self._bus_watch, self._poll_position, self._ticker,
                                        self.scheduler.run, self.takes.run)]
        log.info(f"chromad running (home {self.home}{', fake devices' if self.fake else ''})")
        if not any(f.type == "lifx" for f in self.store.fixtures.values()):
            # first start (or an earlier import found nothing): bring the bulbs into the rig
            self._spawn(self.rig.discover(quiet=True))
        try:
            await self._stop.wait()
        finally:
            await self.shutdown(engine_task)

    async def shutdown(self, engine_task=None) -> None:
        self.takes.stop_rec("engine stopped")
        await self.ipc.stop()
        if self._save_task is not None:
            self._save_task.cancel()
        try:
            self.save_console()
        except OSError as e:
            log.warning(f"could not save console.json: {e}")
        if self.scheduler.latency_stats():
            log.info(f"console output latency (ms): {self.scheduler.latency_stats()}")
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
