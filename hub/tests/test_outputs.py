# M3: outputs — HSB, LIFX (direct addressing, power, blink), virtual, discovery.

import pytest

from fixtures import Fixture
from outputs.base import HSB
from outputs.lifx import LifxOutput, discover_bulbs
from outputs.virtual import VirtualOutput
from testing.clock import FakeClock
from testing.fake_lifx import FakeLifxLAN, FakeLight, bedroom_bulbs, light_factory_for, living_room_bulbs


def lifx_fx(ip="192.168.0.31", mac="d0:73:d5:00:00:01"):
    return Fixture("tv-left", "TV Left", "lifx", "active", "slow", 0.3, 0.25, ip=ip, mac=mac)


def test_hsb_conversions():
    assert HSB(0, 1, 1).hex() == "#ff0000" and HSB(240, 1, 0.5).hex() == "#000080"
    assert HSB(370, 2, -1).clamped() == HSB(10, 1, 0)
    c = HSB.from_rgb((0, 255, 0), b=0.4)
    assert (round(c.h), c.s, c.b) == (120, 1.0, 0.4)


def test_lifx_push_powers_on_once_and_never_off():
    bulbs = living_room_bulbs()
    out = LifxOutput(kelvin=3500, light_factory=light_factory_for(bulbs))
    out.push(lifx_fx(), HSB(180, 0.5, 0.0), 50)
    out.push(lifx_fx(), HSB(180, 0.5, 0.8), 50)
    out.push(lifx_fx(), HSB(180, 0.5, 0.9), 0)
    calls = bulbs[0].calls
    assert [c[0] for c in calls] == ["set_color", "set_color", "set_power", "set_color"]
    assert calls[0][1] == (32768, 32768, 0, 3500) and calls[0][2] == 50
    assert calls[2][1] == 65535                          # on — and never "off"
    out.forget_power()
    out.push(lifx_fx(), HSB(0, 0, 0.5), 0)
    assert calls[-1][0] == "set_power"


def test_lifx_push_errors_are_contained():
    bulbs = living_room_bulbs()
    bulbs[0].fail = True
    out = LifxOutput(light_factory=light_factory_for(bulbs))
    out.push(lifx_fx(), HSB(0, 1, 1), 0)                 # logs, does not raise
    out.push(lifx_fx(ip="10.0.0.1"), HSB(0, 1, 1), 0)    # unknown bulb: same


def test_lifx_blink_is_a_gentle_transient_pulse():
    bulbs = living_room_bulbs()
    LifxOutput(light_factory=light_factory_for(bulbs)).blink(lifx_fx())
    (name, color, period, cycles, waveform, rapid, _t), = bulbs[0].calls
    assert name == "set_waveform" and period == 1000 and cycles == 2   # 1 Hz, within the 3 Hz cap
    assert color[2] < 0.2 * 65535                          # dim, not a flash to full
    assert rapid is False                                  # acked: a lost packet is an error, not "ok"


def test_lifx_blink_refuses_a_bulb_that_is_off_and_leaves_its_power_alone():
    from outputs.lifx import BulbOff
    bulbs = living_room_bulbs()
    bulbs[0].power = 0
    with pytest.raises(BulbOff, match="TV Left is switched off"):
        LifxOutput(light_factory=light_factory_for(bulbs)).blink(lifx_fx())
    assert bulbs[0].calls == [] and bulbs[0].power == 0


def test_lifx_blink_raises_when_the_bulb_does_not_answer():
    bulbs = living_room_bulbs()
    bulbs[0].fail = True
    with pytest.raises(OSError):
        LifxOutput(light_factory=light_factory_for(bulbs)).blink(lifx_fx())


def test_ip_only_fixture_uses_the_zero_mac():
    seen = []
    out = LifxOutput(light_factory=lambda mac, ip: seen.append((mac, ip)) or FakeLight("x", ip, mac))
    out.push(lifx_fx(mac=None), HSB(0, 0, 0.5), 0)
    assert seen == [("00:00:00:00:00:00", "192.168.0.31")]


def test_virtual_output_records_frames_and_current_colour():
    clock = FakeClock()
    v = VirtualOutput(clock)
    fx = Fixture("v1", "V1", "virtual", "active", "fast")
    v.push(fx, HSB(240, 1, 1), 0)
    clock.advance(0.1)
    v.push(fx, HSB(0, 1, 1), 0)
    assert v.hex_for("v1") == "#ff0000" and v.hex_for("nope") is None
    assert [round(t - 1000, 3) for t, *_ in v.frames] == [0.0, 0.1]
    steps = v.blink(fx)
    assert [d for d, _ in steps] == [0.0, 0.5, 1.0, 1.5] and steps[-1][1] == HSB(0, 1, 1)


def test_discover_bulbs_reads_label_ip_mac_group():
    lan = lambda: FakeLifxLAN(living_room_bulbs()[:1] + bedroom_bulbs()[:1])    # noqa: E731
    assert discover_bulbs(lan) == [
        {"label": "TV Left", "ip": "192.168.0.31", "mac": "d0:73:d5:00:00:01", "group": "Living Room"},
        {"label": "Bright Light", "ip": "192.168.0.60", "mac": "d0:73:d5:00:00:06", "group": "Bedroom"}]


def test_real_lifx_light_is_refused_in_tests():
    """The default factory builds a real lifxlan.Light — the test sandbox refuses it."""
    with pytest.raises(Exception, match="real devices"):
        LifxOutput()._light(lifx_fx())
