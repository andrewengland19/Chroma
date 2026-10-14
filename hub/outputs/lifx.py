# =============================================================================
# hub/outputs/lifx.py — LIFX bulbs addressed directly by MAC + IP (no discovery
# per push), the same way lights_agent addresses them.
#
# The AUTO art painter keeps using engine.LifxController.push_aligned exactly as
# before; this output is the console's path (BOARD) and the Setup "Test" blink.
# D15: level 0 = brightness 0 (never powered off); a bulb is powered on only when
# a brightness > 0 is sent and it isn't known to be on.
# =============================================================================

from __future__ import annotations

import logging
from typing import Callable, Optional

from .base import HSB

log = logging.getLogger("chroma-hub")


def _default_light_factory(mac: str, ip: str):
    from lifxlan import Light
    return Light(mac, ip)


class LifxOutput:
    klass = "slow"
    capabilities = frozenset({"color", "brightness", "fade", "power", "waveform"})

    def __init__(self, kelvin: int = 3500, light_factory: Optional[Callable] = None):
        self.kelvin = kelvin
        self._factory = light_factory or _default_light_factory
        self._lights: dict = {}
        self._powered: set = set()

    def _light(self, f):
        if not f.ip:
            raise ValueError(f"LIFX fixture {f.id} has no IP address")
        mac = f.mac or "00:00:00:00:00:00"        # IP-addressed, like lights_agent's scene recall
        key = (mac, f.ip)
        light = self._lights.get(key)
        if light is None:
            light = self._lights[key] = self._factory(mac, f.ip)
        return light

    def hsbk(self, hsb: HSB) -> tuple:
        c = hsb.clamped()
        return (int(round(c.h / 360.0 * 65535)) % 65536, int(round(c.s * 65535)),
                int(round(c.b * 65535)), self.kelvin)

    def push(self, f, hsb: HSB, fade_ms: int = 0) -> None:
        try:
            light = self._light(f)
            light.set_color(self.hsbk(hsb), duration=int(fade_ms), rapid=True)
            if hsb.b > 0 and f.ip not in self._powered:
                light.set_power("on", duration=0, rapid=True)
                self._powered.add(f.ip)
        except Exception as e:                       # never raise into the engine
            log.warning(f"LIFX push to {f.label} failed: {e}")

    def forget_power(self) -> None:
        """Bulbs may have been switched off elsewhere (lights agent, lite): re-check on next push."""
        self._powered.clear()

    def blink(self, f) -> None:
        """Two transient 1 Hz pulses to a dim level; the bulb returns to its own colour
        afterwards (LIFX waveform, transient=1). Power state is not changed. Blocking:
        with a known MAC both messages wait for the bulb's ack (retried, UDP drops packets),
        so an unreachable bulb raises Unreachable instead of "succeeding", and a bulb that
        is off raises BulbOff (a pulse would be invisible). An IP-only fixture can't be
        acked (lifxlan matches replies by MAC), so it gets the fire-and-forget pulse."""
        light = self._light(f)
        wave = (1, (0, 0, int(0.15 * 65535), self.kelvin), 1000, 2, 0, 1)        # SINE
        if not f.mac:
            light.set_waveform(*wave, rapid=True)
            return
        if not _with_retries(f, light.get_power):
            raise BulbOff(f"{f.label} is switched off — turn it on to see the Test blink")
        _with_retries(f, lambda: light.set_waveform(*wave, rapid=False))


ATTEMPTS = 3                                     # lifxlan tries once, 1 s each


def _with_retries(f, call):
    for i in range(ATTEMPTS):
        try:
            return call()
        except Exception as e:
            if i == ATTEMPTS - 1:
                log.info(f"LIFX {f.label} at {f.ip}: no reply ({e})")
                raise Unreachable(f"could not reach {f.label} at {f.ip} (no reply)") from None


class Unreachable(Exception):
    """The bulb did not answer."""


class BulbOff(Exception):
    """The bulb answered but is powered off."""


def discover_bulbs(lan_factory: Optional[Callable] = None) -> list:
    """Every LIFX bulb on the LAN: [{label, ip, mac, group}]. Blocking (network) — run
    it in an executor. Errors on one bulb skip that bulb."""
    if lan_factory is None:
        from lifxlan import LifxLAN
        lan_factory = LifxLAN
    out = []
    for light in lan_factory().get_lights():
        try:
            out.append({"label": str(light.get_label()), "ip": str(light.get_ip_addr()),
                        "mac": str(light.get_mac_addr()).lower(), "group": str(light.get_group_label())})
        except Exception as e:
            log.warning(f"LIFX discovery: skipping a bulb ({e})")
    return out
