# M1: CHROMA_HOME redirect (default unchanged) and the Engine/provider seams chromad uses.

import asyncio
import os
import subprocess
import sys

from conftest import HUB, write_config
from testing.clock import FakeClock
from testing.fake_provider import FakeProvider


def _paths(env_extra: dict) -> list:
    env = {k: v for k, v in os.environ.items() if k != "CHROMA_HOME"}
    env.update(env_extra)
    code = ("import config, layout, scenes, engine, providers, showctl;"
            "print(config.CONFIG_PATH); print(layout.LAYOUT_PATH); print(scenes.SCENES_PATH);"
            "print(engine.STATE_FILE); print(providers.CREDENTIALS_PATH); print(showctl.PID_FILE)")
    out = subprocess.run([sys.executable, "-c", code], cwd=HUB, env=env, capture_output=True,
                         text=True, check=True).stdout.split()
    return out


def test_default_paths_are_unchanged(tmp_path):
    home = str(tmp_path / "home")
    assert _paths({"HOME": home}) == [f"{home}/.chroma/{n}" for n in
                                      ("config.json", "layout.json", "scenes.json", "state.json",
                                       "atv_credentials.json", "hub.pid")]


def test_chroma_home_redirects_everything(tmp_path):
    alt = str(tmp_path / "alt")
    got = _paths({"HOME": str(tmp_path / "home"), "CHROMA_HOME": alt})
    assert all(p.startswith(alt + "/") for p in got), got


def test_nowplaying_carries_position_and_duration():
    from providers import NowPlaying
    clock = FakeClock()
    prov = FakeProvider(clock=clock)
    clock.advance(42.7)
    np = NowPlaying.from_playing(prov.playing())
    assert (np.pos_ms, np.dur_ms) == (42_000, 205_000)
    assert np.track_id() == prov.track.hash          # dedup key unchanged


def test_raw_anchor_reads_mrp_state_and_survives_missing_fields():
    from providers import COCOA_EPOCH, AppleTVProvider

    class St:
        def __init__(self, fields):
            self.f = fields

        def metadata_field(self, k):
            return self.f.get(k)

    class Psm:
        def __init__(self, fields):
            self.playing = St(fields)

    class Inst:
        def __init__(self, fields):
            self.psm = Psm(fields)

    class Meta:
        def __init__(self, fields, others=()):
            self.main_instance = Inst(fields)
            self.instances = [self.main_instance, *[Inst(o) for o in others]]

    class Atv:
        def __init__(self, fields, others=()):
            self.metadata = Meta(fields, others)

    class Cfg:
        name = "TV"

    p = AppleTVProvider(Atv({"elapsedTime": 61.25, "elapsedTimeTimestamp": 813300000.5,
                             "playbackRate": 1.0}), Cfg())
    assert p.raw_anchor() == {"elapsed_s": 61.25, "anchor_wall": 813300000.5 + COCOA_EPOCH, "rate": 1.0}
    assert AppleTVProvider(Atv({"elapsedTime": 3.0}), Cfg()).raw_anchor() is None
    # the first instance lacks the anchor, another has it
    p2 = AppleTVProvider(Atv({"elapsedTime": 3.0}, [{"elapsedTime": 4.5, "elapsedTimeTimestamp": 10.0}]), Cfg())
    assert p2.raw_anchor()["elapsed_s"] == 4.5
    broken = AppleTVProvider(object(), Cfg())
    assert broken.raw_anchor() is None


async def test_engine_run_uses_injected_provider_and_lifx():
    import engine as E
    from testing.fake_lifx import make_fake_lifx_controller
    from config import Config
    write_config(reconcile_seconds=0.05)
    prov = FakeProvider(clock=FakeClock())

    async def factory(loop):
        return prov

    eng = E.Engine(asyncio.get_running_loop(), install_signals=False, provider_factory=factory,
                   lifx=make_fake_lifx_controller(Config.load()))
    seen = []
    eng.np_listeners.append(lambda np, src: seen.append((np.title if np else None, src)))
    task = asyncio.create_task(eng.run())
    for _ in range(300):
        await asyncio.sleep(0.01)
        if ("Older", "prime") in seen and all(b.colors_set() for b in eng.lifx.fake_bulbs):
            break
    assert ("Older", "prime") in seen
    assert eng.lifx.count == 5 and all(b.colors_set() for b in eng.lifx.fake_bulbs)
    assert prov._push_listener is eng
    await asyncio.sleep(0.12)
    assert any(src == "reconcile" for _, src in seen)     # listener sees every sample, pre-dedup
    eng.stop()
    await asyncio.wait_for(task, timeout=2)


async def test_listener_errors_never_reach_the_engine(make_engine):
    eng, bulbs, prov = await make_engine()

    def boom(np, src):
        raise RuntimeError("listener bug")
    eng.np_listeners.append(boom)
    await eng._on_playing(await prov.now_playing(), "push")
    assert all(b.colors_set() for b in bulbs)
