# M0 AC: FakeProvider produces a scripted 3-minute track with changing position,
# pause/resume, seek and track change — and drives the real Engine's push path.

import asyncio

from pyatv.const import DeviceState

from testing.clock import FakeClock
from testing.fake_provider import FakeProvider, FakeTrack, default_tracks


def three_minute(clock, **kw):
    tracks = [FakeTrack("Three", "Fake Artist", "Fake Album", 180_000),
              FakeTrack("Next", "Fake Artist", "Fake Album", 60_000)]
    return FakeProvider(tracks, clock=clock, **kw)


def test_position_advances_pauses_resumes_and_seeks():
    clock = FakeClock()
    p = three_minute(clock)
    assert p.position_ms() == 0 and p.is_playing
    clock.advance(42.5)
    assert p.position_ms() == 42_500
    pl = p.playing()
    assert (pl.position, pl.total_time, pl.device_state) == (42, 180, DeviceState.Playing)

    p.pause()
    clock.advance(30)
    assert p.position_ms() == 42_500 and p.playing().device_state == DeviceState.Paused
    p.play()
    clock.advance(10)
    assert p.position_ms() == 52_500

    p.seek(150_000)
    clock.advance(1.25)
    assert p.position_ms() == 151_250
    anchor = p.elapsed_anchor()
    assert anchor["elapsed_s"] == 151.25 and anchor["rate"] == 1.0


def test_scripted_three_minutes_with_track_change():
    clock = FakeClock()
    p = three_minute(clock, script=[(60, "pause"), (75, "play"), (100, "seek", 170_000)])
    samples = []
    for _ in range(0, 200):          # 200 s in 1 s steps
        clock.advance(1)
        p.tick()
        samples.append((p.index, round(p.position_ms())))
    assert samples[29] == (0, 30_000)
    assert samples[59] == (0, 60_000) and samples[70] == (0, 60_000)      # paused 60–75 s
    assert samples[80] == (0, 66_000)                                      # resumed at 75 s (t=81)
    assert samples[99] == (0, 170_000)                                     # seek at 100 s
    assert samples[109] == (1, 0)                                          # 180 s → next track
    assert samples[119] == (1, 10_000)
    assert samples[-1][0] == 1 and samples[-1][1] == 60_000                # last track ends, holds
    assert not p.is_playing


async def test_engine_push_path(make_engine):
    clock = FakeClock()
    prov = FakeProvider(default_tracks(), clock=clock)
    eng, bulbs, _ = await make_engine(provider=prov)
    prov.start_push(eng)
    prov.set_device_listener(eng)
    prov.skip(1)                          # → Engine.playstatus_update → _on_playing
    for _ in range(200):                  # painting runs in an executor; wait for it
        await asyncio.sleep(0.01)
        if all(b.colors_set() for b in bulbs):
            break
    assert eng._last_np.title == "Levitating (Remix)"
    assert all(b.colors_set() for b in bulbs)
    prov.drop_connection()
    await asyncio.sleep(0.01)
    assert eng._disconnected.is_set()


async def test_transport_and_close():
    prov = FakeProvider(clock=FakeClock())
    await prov.next()
    await prov.play_pause()
    assert prov.calls == ["next", "play_pause"] and prov.index == 1 and not prov.is_playing
    np = await prov.now_playing()
    assert np.title == "Levitating (Remix)" and np.playing is False
    assert await prov.artwork_bytes()
    await prov.close()
    assert await prov.now_playing() is None


def test_empty_playlist_skip_is_a_noop():
    p = FakeProvider([], clock=FakeClock())
    p.skip(1)
    assert p.track is None and not p.is_playing


def test_parked_at_end_without_auto_advance_is_not_playing():
    clock = FakeClock()
    p = three_minute(clock, auto_advance=False)
    clock.advance(200)
    p.tick()
    assert p.position_ms() == 180_000 and not p.is_playing
    assert p.elapsed_anchor()["rate"] == 0.0
    assert p.playing().device_state == DeviceState.Paused
