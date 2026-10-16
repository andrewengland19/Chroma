# OutputScheduler on a fake clock: coalescing, per-class pacing, strobe hold, latency.

from outputs.base import HSB
from scheduler import OutputScheduler


class Rig:
    def __init__(self):
        self.t = 0.0
        self.sent = []
        self.s = OutputScheduler(lambda: self.t, lambda f, h, fade: self.sent.append((self.t, f, h, fade)),
                                 lambda f: "fast" if f.startswith("v") else "slow", lambda f: (1.0, 3.0))


def test_newest_value_wins_and_lifx_is_paced_at_20_per_second():
    r = Rig()
    r.s.submit({"bulb": HSB(0, 1, 0.50)}, 0)
    assert r.s.step(0) is None and len(r.sent) == 1
    for i in range(5):                                    # 5 values inside 50 ms
        r.t = 10 + i
        r.s.submit({"bulb": HSB(0, 1, 0.51 + 0.01 * i)}, r.t)
        assert r.s.step() == 50.0                          # wait for the slot
    r.t = 50
    r.s.step()
    assert [x[2].b for x in r.sent] == [0.50, 0.55]        # one message, the newest value


def test_virtual_is_paced_at_60hz():
    r = Rig()
    r.s.submit({"v1": HSB(0, 1, 0.5)}, 0)
    r.s.step(0)
    r.t = 5
    r.s.submit({"v1": HSB(0, 1, 0.52)}, 5)
    assert r.s.step() == 1000 / 60


def test_identical_values_are_not_resent_and_primed_values_count():
    r = Rig()
    r.s.prime({"bulb": HSB(10, 1, 0.5)})
    r.s.submit({"bulb": HSB(10, 1, 0.5)}, 0)
    r.s.step(0)
    assert r.sent == []


def test_strobe_hold_is_released_by_step_with_the_intent_time():
    r = Rig()
    r.s.prime({"v1": HSB(0, 1, 0.0)})
    for i in range(11):                                   # on/off every 20 ms, ending on "on"
        r.t = i * 20
        r.s.submit({"v1": HSB(0, 1, 1.0 if i % 2 == 0 else 0.0)}, r.t)
        r.s.step()
    assert len(r.sent) == 6                                # 6 edges, then held
    nxt = r.s.step(r.t)
    assert nxt == 1000.0                                    # first edge at 0 + 1 s window
    r.t = 1000
    r.s.step()
    assert len(r.sent) == 7 and r.sent[-1][2].b == 1.0     # the newest held value
    assert r.s.latency_stats()["fast"]["max"] == 1000 - 200  # since the intent of the value sent


def test_reset_forgets_everything():
    r = Rig()
    r.s.submit({"bulb": HSB(0, 1, 0.5)}, 0)
    r.s.reset()
    assert r.s.step(0) is None and r.sent == []
