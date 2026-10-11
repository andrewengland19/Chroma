# M1: SongClock — interpolation, pause, slew-limited resync, seek and track change,
# with precise (MRP anchor) and coarse (pyatv whole-second) samples. FakeClock only.

import pytest

from clock import ClockSample, SongClock
from testing.clock import FakeClock
from testing.fake_provider import FakeProvider, FakeTrack


def precise(track, pos_ms, clock, playing=True, dur=200_000):
    return ClockSample(track, playing, int(pos_ms // 1000) * 1000, dur,
                       {"elapsed_s": pos_ms / 1000.0, "anchor_wall": clock.time(), "rate": 1.0 if playing else 0.0})


def coarse(track, pos_ms, playing=True, dur=200_000):
    return ClockSample(track, playing, int(pos_ms // 1000) * 1000, dur, None)


@pytest.fixture
def c():
    return FakeClock()


def test_no_track_no_position(c):
    assert SongClock(c).pos_ms() is None


def test_interpolates_while_playing_and_freezes_on_pause(c):
    sc = SongClock(c)
    assert sc.update(precise("t1", 10_000, c)) == "track"
    c.advance(2.5)
    assert sc.pos_ms() == pytest.approx(12_500)
    assert sc.update(precise("t1", 12_500, c, playing=False)) == "pause"
    c.advance(10)
    assert sc.pos_ms() == pytest.approx(12_500)
    assert sc.update(precise("t1", 12_500, c)) == "play"
    c.advance(1)
    assert sc.pos_ms() == pytest.approx(13_500)


def test_anchor_age_is_extrapolated(c):
    sc = SongClock(c)
    s = ClockSample("t1", True, 9000, 200_000, {"elapsed_s": 9.8, "anchor_wall": c.time() - 0.7, "rate": 1.0})
    sc.update(s)
    assert sc.pos_ms() == pytest.approx(10_500)


@pytest.mark.parametrize("err", [30, 400, 1200, -20, -50])
def test_small_errors_apply_immediately(c, err):
    sc = SongClock(c)
    sc.update(precise("t1", 50_000, c))
    c.advance(1)
    assert sc.update(precise("t1", 51_000 + err, c)) == "resync"
    assert sc.pos_ms() == pytest.approx(51_000 + err)
    assert sc.last_err_ms == pytest.approx(err) and sc.max_err_ms == pytest.approx(abs(err))


def test_large_backward_error_is_slewed_never_jumps_back_more_than_50ms(c):
    sc = SongClock(c)
    sc.update(precise("t1", 50_000, c))
    c.advance(1)
    before = sc.pos_ms()                                  # 51 000
    sc.update(precise("t1", 51_000 - 300, c))             # we're 300 ms ahead
    after = sc.pos_ms()
    assert before - after == pytest.approx(50)            # only 50 ms back right away
    prev = after
    for _ in range(40):                                   # 4 s at 10 Hz: never goes backward
        c.advance(0.1)
        p = sc.pos_ms()
        assert p > prev
        prev = p
    # 250 ms of debt at 100 ms/s is absorbed after 2.5 s → now locked to truth
    truth = 51_000 - 300 + 4_000
    assert sc.pos_ms() == pytest.approx(truth)


@pytest.mark.parametrize("jump", [1500, 30_000, -1500, -40_000])
def test_seek_snaps_and_reports(c, jump):
    events = []
    sc = SongClock(c, on_event=lambda e, _: events.append(e))
    sc.update(precise("t1", 60_000, c))
    c.advance(1)
    assert sc.update(precise("t1", 61_000 + jump, c)) == "seek"
    assert sc.pos_ms() == pytest.approx(61_000 + jump)
    assert events == ["track", "seek"]
    assert sc.max_err_ms == 0                             # seeks are not sync error


def test_track_change_resets(c):
    sc = SongClock(c)
    sc.update(precise("t1", 60_000, c))
    c.advance(1)
    sc.update(precise("t1", 61_300, c))
    assert sc.max_err_ms == pytest.approx(300)
    assert sc.update(precise("t2", 0, c, dur=180_000)) == "track"
    assert sc.pos_ms() == 0 and sc.dur_ms == 180_000 and sc.max_err_ms == 0


def test_clamped_to_duration(c):
    sc = SongClock(c)
    sc.update(precise("t1", 199_000, c))
    c.advance(5)
    assert sc.pos_ms() == 200_000


def test_coarse_samples_are_intervals_not_points(c):
    sc = SongClock(c)
    sc.update(coarse("t1", 10_000))                       # unknown sub-second → midpoint 10.5 s
    assert sc.pos_ms() == pytest.approx(10_500)
    c.advance(0.4)                                        # est 10.9 s; truth somewhere in [10, 11)
    assert sc.update(coarse("t1", 10_000)) is None        # inside the interval: no correction
    c.advance(0.4)                                        # est 11.3 s
    assert sc.update(coarse("t1", 11_000)) is None
    c.advance(1.0)                                        # est 12.3; sample says [13, 14) → we're behind
    assert sc.update(coarse("t1", 13_000)) == "resync"
    assert sc.pos_ms() >= 13_000 - SongClock.COARSE_TOL_MS


def test_coarse_tracking_of_a_real_track_never_saws_back(c):
    """pyatv-style whole-second samples at 4 Hz for 2 minutes: position stays within
    the truncation window and never steps back more than 50 ms."""
    prov = FakeProvider([FakeTrack("A", "B", "C", 300_000)], clock=c)
    sc = SongClock(c)
    prev = None
    for i in range(480):
        c.advance(0.25)
        np = prov.playing()
        sc.update(ClockSample(np.hash, True, np.position * 1000, np.total_time * 1000, None))
        p, truth = sc.pos_ms(), prov.position_ms()
        assert -SongClock.COARSE_TOL_MS - 1 <= p - truth <= 1000 + SongClock.COARSE_TOL_MS
        if prev is not None:
            assert p - prev >= -SongClock.MAX_BACK_MS - 1e-6
        prev = p


def test_precise_tracking_is_exact(c):
    prov = FakeProvider([FakeTrack("A", "B", "C", 300_000)], clock=c)
    sc = SongClock(c)
    for i in range(120):
        c.advance(0.5)
        if i == 60:
            prov.pause()
        if i == 70:
            prov.play()
        if i == 90:
            prov.seek(200_000)
        np = prov.playing()
        sc.update(ClockSample(np.hash, prov.is_playing, np.position * 1000, np.total_time * 1000,
                              prov.raw_anchor()))
        assert sc.pos_ms() == pytest.approx(prov.position_ms(), abs=0.5)
        assert sc.source == "precise"


def test_listener_errors_are_contained(c):
    def boom(e, sc):
        raise RuntimeError("x")
    sc = SongClock(c, on_event=boom)
    assert sc.update(precise("t1", 0, c)) == "track"


def test_anchor_age_is_signed_for_clock_skew(c):
    """ATV clock 150 ms ahead of the Mac: the anchor looks 150 ms 'in the future'."""
    sc = SongClock(c)
    sc.update(ClockSample("t1", True, 9000, 200_000, {"elapsed_s": 9.0, "anchor_wall": c.time() + 0.15, "rate": 1.0}))
    assert sc.pos_ms() == pytest.approx(8_850)
