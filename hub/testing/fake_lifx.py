# =============================================================================
# hub/testing/fake_lifx.py — lifxlan stand-ins that record every call.
#
# FakeLight mimics the lifxlan.Light methods the hub uses; FakeLifxLAN mimics
# LifxLAN().get_lights(). install_fake_bulbs() drops fakes into a real
# LifxController so the AUTO painting path runs unmodified in tests.
# =============================================================================

from __future__ import annotations

import time
from typing import Optional

# The five Living Room bulbs (real labels; mockup names in DECISIONS.md D5).
LIVING_ROOM = [
    ("TV Left", "192.168.0.31", "d0:73:d5:00:00:01"),
    ("TV Right", "192.168.0.26", "d0:73:d5:00:00:02"),
    ("Couch Left", "192.168.0.44", "d0:73:d5:00:00:03"),
    ("Right couch", "192.168.0.45", "d0:73:d5:00:00:04"),
    ("Washer", "192.168.0.52", "d0:73:d5:00:00:05"),
]


class FakeLight:
    def __init__(self, label: str, ip: str = "192.168.0.99", mac: str = "d0:73:d5:00:00:99",
                 group: str = "Living Room", clock=None):
        self.label = label
        self.ip = ip
        self.mac = mac
        self.group = group
        self.power = 65535
        self.color = (0, 0, 32768, 3500)
        self.calls: list = []        # ("set_color", hsbk, duration, rapid, t) / ("set_power", ...)
        self._clock = clock
        self.fail = False            # raise on the next call (error-path tests)

    def _t(self) -> float:
        return self._clock.monotonic() if self._clock else time.monotonic()

    def _maybe_fail(self):
        if self.fail:
            raise OSError(f"fake failure on {self.label}")

    # lifxlan.Light surface
    def get_label(self) -> str:
        return self.label

    def get_ip_addr(self) -> str:
        return self.ip

    def get_mac_addr(self) -> str:
        return self.mac

    def get_group(self) -> str:
        return self.group

    def get_group_label(self) -> str:
        return self.group

    def get_power(self) -> int:
        return self.power

    def get_color(self) -> tuple:
        return self.color

    def set_color(self, color, duration: int = 0, rapid: bool = False) -> None:
        self._maybe_fail()
        self.color = tuple(int(c) for c in color)
        self.calls.append(("set_color", self.color, duration, rapid, self._t()))

    def set_power(self, power, duration: int = 0, rapid: bool = False) -> None:
        self._maybe_fail()
        on = power in ("on", True, 1, 65535) or (isinstance(power, int) and power > 0)
        self.power = 65535 if on else 0
        self.calls.append(("set_power", self.power, duration, rapid, self._t()))

    def set_waveform(self, is_transient, color, period, cycles, duty_cycle, waveform, rapid=False) -> None:
        self._maybe_fail()
        self.calls.append(("set_waveform", tuple(color), period, cycles, waveform, self._t()))

    def colors_set(self) -> list:
        return [c[1] for c in self.calls if c[0] == "set_color"]


# Discovered but not in the rig by default (shown as "not added yet").
BEDROOM = [
    ("Bright Light", "192.168.0.60", "d0:73:d5:00:00:06"),
    ("Cedar Chest", "192.168.0.61", "d0:73:d5:00:00:07"),
    ("Desk 2", "192.168.0.62", "d0:73:d5:00:00:08"),
]


class FakeLifxLAN:
    def __init__(self, lights: list):
        self._lights = lights

    def get_lights(self) -> list:
        return list(self._lights)


def living_room_bulbs(clock=None) -> list:
    return [FakeLight(lbl, ip, mac, clock=clock) for lbl, ip, mac in LIVING_ROOM]


def bedroom_bulbs(clock=None) -> list:
    return [FakeLight(lbl, ip, mac, group="Bedroom", clock=clock) for lbl, ip, mac in BEDROOM]


def light_factory_for(bulbs: list):
    """A LifxOutput light factory that resolves (mac, ip) to one of these fake bulbs."""
    by_ip = {b.ip: b for b in bulbs}

    def factory(mac: str, ip: str):
        if ip not in by_ip:
            raise OSError(f"no fake bulb at {ip}")
        return by_ip[ip]
    return factory


def install_fake_bulbs(controller, bulbs: Optional[list] = None) -> list:
    """Put fake bulbs into a real LifxController (discovery order = list order)."""
    bulbs = bulbs if bulbs is not None else living_room_bulbs()
    controller._lights = list(bulbs)
    controller._labels = [b.label for b in bulbs]
    return bulbs


def make_fake_lifx_controller(cfg, bulbs: Optional[list] = None, clock=None):
    """A real LifxController whose discover() finds fake bulbs (for `chromad --fake`)."""
    from engine import LifxController

    class FakeLifxController(LifxController):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.fake_bulbs = bulbs if bulbs is not None else living_room_bulbs(clock)

        def discover(self) -> int:
            install_fake_bulbs(self, self.fake_bulbs)
            return self.count

    return FakeLifxController(cfg)
