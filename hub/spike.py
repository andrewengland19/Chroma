#!/usr/bin/env python3
# =============================================================================
# hub/spike.py  —  Pass 1 make-or-break proof.
#
# Connects to the paired Apple TV, listens for track changes over pyatv's push
# updater, pulls album artwork, runs it through the existing color pipeline, and
# (capstone) pushes the palette to LIFX bulbs — all with Apple Music QUIT on the
# Mac. This is the demo that validates the whole untether thesis.
#
# Run:  python hub/spike.py
#       CHROMA_LIFX_GROUP="Living Room" python hub/spike.py   # limit to a group
#       CHROMA_NO_LIGHTS=1 python hub/spike.py                # skip bulbs (steps 1-4 only)
# =============================================================================

import asyncio
import os
import sys

from providers import AppleTVProvider, NowPlaying
from color_pipeline import artwork_to_hsbk

ARTWORK_OUT = os.path.expanduser("~/.chroma/atv_artwork.jpg")
LIFX_GROUP = os.environ.get("CHROMA_LIFX_GROUP")  # None = all lights on LAN
SKIP_LIGHTS = os.environ.get("CHROMA_NO_LIGHTS") == "1"


# =============================================================================
# LIFX (sync lifxlan, run off the event loop via executors)
# =============================================================================

def _discover_lights():
    try:
        from lifxlan import LifxLAN
    except ImportError:
        print("  (lifxlan not available — running without bulbs)")
        return []
    lan = LifxLAN()
    lights = lan.get_lights()
    if LIFX_GROUP:
        lights = [l for l in lights if l.get_group() == LIFX_GROUP]
    for l in lights:
        try:
            print(f"     • {l.get_label()} @ {l.get_ip_addr()}")
        except Exception:
            pass
    return lights


def _push(lights, hsbk_colors):
    for i, light in enumerate(lights):
        color = hsbk_colors[i % len(hsbk_colors)]
        try:
            light.set_color(color, duration=1500, rapid=True)
        except Exception as e:
            print(f"     ! failed to set a light: {e}")


# =============================================================================
# Per-track handling: artwork → pipeline → bulbs
# =============================================================================

async def handle_track(provider: AppleTVProvider, np: NowPlaying, lights, loop):
    print(f"\n♪  {np.label()}   [{np.album}]   ({'playing' if np.playing else 'paused'})")

    art = await provider.artwork_bytes()
    if not art:
        print("   → no artwork available for this track — skipping")
        return
    os.makedirs(os.path.dirname(ARTWORK_OUT), exist_ok=True)
    with open(ARTWORK_OUT, "wb") as f:
        f.write(art)
    print(f"   → artwork saved ({len(art)} bytes) → {ARTWORK_OUT}")

    count = min(3, len(lights)) if lights else 3
    hsbk = artwork_to_hsbk(art, count)
    if not hsbk:
        print("   → artwork is majority white — pipeline correctly skipped it")
        return
    print(f"   → HSBK palette: {hsbk}")

    if lights and not SKIP_LIGHTS:
        await loop.run_in_executor(None, _push, lights, hsbk)
        print(f"   → pushed {len(hsbk)} color(s) to {len(lights)} light(s)  🎨")


# =============================================================================
# Push listener — event-driven track changes (beats the old 2s poll)
# =============================================================================

class Listener:
    def __init__(self, provider, lights, loop):
        self.provider = provider
        self.lights = lights
        self.loop = loop
        self.last_track_id = None

    def playstatus_update(self, updater, playstatus):
        np = NowPlaying(
            title=playstatus.title or "",
            artist=playstatus.artist or "",
            album=playstatus.album or "",
            playing=str(playstatus.device_state) == "DeviceState.Playing",
        )
        tid = np.track_id()
        if not np.title and not np.artist:
            return
        if tid == self.last_track_id:
            return
        self.last_track_id = tid
        # Fetch artwork + push off the callback (artwork is a fresh await).
        asyncio.ensure_future(
            handle_track(self.provider, np, self.lights, self.loop), loop=self.loop
        )

    def playstatus_error(self, updater, exception):
        print(f"   ! push error: {exception}")


async def main():
    loop = asyncio.get_event_loop()

    print("Connecting to the paired Apple TV…")
    try:
        provider = await AppleTVProvider.connect(loop)
    except RuntimeError as e:
        print(f"  ✗  {e}")
        sys.exit(1)
    print(f"  ✓  connected to {provider.name}")

    lights = []
    if not SKIP_LIGHTS:
        print("\nDiscovering LIFX lights…")
        lights = await loop.run_in_executor(None, _discover_lights)
        print(f"  {len(lights)} light(s)" + (f" in group '{LIFX_GROUP}'" if LIFX_GROUP else ""))

    # Prime once with whatever is playing right now.
    np = await provider.now_playing()
    if np and (np.title or np.artist):
        await handle_track(provider, np, lights, loop)
    else:
        print("\nNothing playing yet — start a track on the Apple TV.")

    # Then react to every track change live.
    listener = Listener(provider, lights, loop)
    if np:
        listener.last_track_id = np.track_id()
    provider.push_updater.listener = listener
    provider.push_updater.start()

    print("\nListening for track changes. Change songs on the Apple TV to test. Ctrl+C to stop.\n")
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await provider.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nbye.")
