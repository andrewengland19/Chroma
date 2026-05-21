#!/usr/bin/env python3
# =============================================================================
# lifx_music_daemon.py
# Author: Andrew England / andrewengland19
# Created: 2026-05-16
# Last Updated: 2026-05-16 13:00:00
#
# Music immersion lighting daemon for a subset of LIFX bulbs.
# Polls Apple Music now-playing state via AppleScript, extracts dominant
# colors from album artwork, and pushes them to LIFX bulbs over LAN.
# Runs continuously; detects track changes and transitions smoothly.
# =============================================================================

import subprocess
import time
import io
import urllib.request
import colorsys
import logging
from dataclasses import dataclass
from typing import Optional

# --- Third-party deps: pip install colorthief lifxlan Pillow
try:
    from colorthief import ColorThief
    from lifxlan import LifxLAN
except ImportError:
    raise ImportError(
        "Missing dependencies. Run:\n"
        "  pip install colorthief lifxlan Pillow\n"
    )

# =============================================================================
# CONFIG — edit these to taste
# =============================================================================

# How often (seconds) to check what's playing
POLL_INTERVAL = 2.0

# Color transition duration in milliseconds (sent to LIFX bulbs)
# 1500ms = smooth, cinematic. Increase for slower fades, decrease for snappy.
TRANSITION_MS = 1500

# ---------------------------------------------------------------------------
# LIGHT GROUP
# Set USE_GROUP = True and set GROUP_NAME to the group label in the LIFX app.
# Set USE_GROUP = False to control all discovered lights on the LAN.
# ---------------------------------------------------------------------------
USE_GROUP = True
GROUP_NAME = "Living Room"

# ---------------------------------------------------------------------------
# SINGLE COLOR MODE
# True  → all controlled bulbs get the same dominant color (unified wash).
# False → each bulb gets a different color from the artwork palette (default).
# ---------------------------------------------------------------------------
SINGLE_COLOR = True

# Number of distinct palette colors to extract when SINGLE_COLOR = False.
# Ignored when SINGLE_COLOR = True. If you have 4 bulbs and set this to 2,
# the colors cycle: bulbs 1&3 get color A, bulbs 2&4 get color B.
NUM_COLORS = 3

# ---------------------------------------------------------------------------
# BRIGHTNESS
#
# BRIGHTNESS (0.0–1.0): master brightness ceiling. All bulbs are scaled so
# the brightest one in the scene hits this value.
#
# BRIGHTNESS_DYNAMIC_RANGE (0.0–2.0): controls how much variation in
# brightness is allowed across bulbs when SINGLE_COLOR = False.
#
#   0.0 → flat: all bulbs same brightness (BRIGHTNESS), no dimming variation.
#   1.0 → natural: preserves the brightness spread as extracted from artwork.
#   2.0 → dramatic: exaggerates the spread — bright bulbs get brighter,
#         dim bulbs get dimmer. Good for moody/dark artwork.
#
# The spread is always centered on BRIGHTNESS, so no bulb ever exceeds 1.0
# or drops below BRIGHTNESS_FLOOR regardless of the range setting.
#
# BRIGHTNESS_FLOOR (0.0–1.0): minimum brightness any bulb will be set to,
# even with a large dynamic range. Prevents bulbs from going nearly off.
# ---------------------------------------------------------------------------
BRIGHTNESS = 0.75
BRIGHTNESS_DYNAMIC_RANGE = 1.0
BRIGHTNESS_FLOOR = 0.25

# Kelvin for white-balance when a color happens to be unsaturated
NEUTRAL_KELVIN = 3500

# ---------------------------------------------------------------------------
# WHITE REJECTION
#
# A color is considered "white/near-white" and discarded if EITHER:
#   • Its HSV saturation (0–255) is below WHITE_SAT_THRESHOLD, OR
#   • Its HSV value/brightness (0–255) is above WHITE_VAL_THRESHOLD
#     while also being low-saturation.
#
# Tuning guide:
#   WHITE_SAT_THRESHOLD = 45  → catches pure white, off-white, light gray,
#                                cream, very pale pastels.
#                                Raise to 60–80 to also catch dusty/muted tones.
#   WHITE_VAL_THRESHOLD = 215 → anything brighter than this AND low-sat = white.
#                                Lower to 200 to be more aggressive.
#
# If ALL candidate colors are white after filtering, the update is skipped
# entirely (no change to lights) rather than defaulting to a white scene.
# ---------------------------------------------------------------------------
WHITE_SAT_THRESHOLD = 45   # saturation 0–255; below this = white candidate
WHITE_VAL_THRESHOLD = 215  # value 0–255; above this + low sat = white

# How many extra palette candidates to pull from ColorThief to compensate for
# colors that will be discarded as white. Pulling more costs a few ms, but
# gives the filter enough material to work with on pale/minimal artwork.
PALETTE_OVERSAMPLE = 6  # e.g. need 3 colors → request 3+6=9 candidates

# Logging verbosity: logging.DEBUG for verbose, logging.INFO for normal
LOG_LEVEL = logging.INFO

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lifx-music")


# =============================================================================
# DATA
# =============================================================================

@dataclass
class NowPlaying:
    title: str = ""
    artist: str = ""
    album: str = ""
    artwork_url: str = ""

    def track_id(self) -> str:
        """Unique key for deduplication; ignores artwork URL differences."""
        return f"{self.artist}|||{self.title}"


# =============================================================================
# APPLE MUSIC — AppleScript bridge
# =============================================================================

APPLESCRIPT_NOW_PLAYING = """
tell application "Music"
    if player state is playing then
        set t to name of current track
        set ar to artist of current track
        set al to album of current track
        -- artwork URL may be empty for streaming tracks; that's OK
        try
            set aw to artwork url of current track
        on error
            set aw to ""
        end try
        return t & "|||" & ar & "|||" & al & "|||" & aw
    else
        return "NOT_PLAYING"
    end if
end tell
"""

APPLESCRIPT_ARTWORK = """
-- Saves current track artwork to a temp file and returns the path.
-- Fallback when artwork URL is unavailable (streaming tracks).
tell application "Music"
    if player state is playing then
        set tmpPath to POSIX path of (path to temporary items folder) & "lifx_artwork.jpg"
        try
            set theTrack to current track
            set theArtwork to artwork 1 of theTrack
            set picData to raw data of theArtwork
            set fileRef to open for access POSIX file tmpPath with write permission
            set eof of fileRef to 0
            write picData to fileRef
            close access fileRef
            return tmpPath
        on error errMsg
            return ""
        end try
    else
        return ""
    end if
end tell
"""


def run_applescript(script: str) -> str:
    """Run an AppleScript and return stdout stripped, or '' on error."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception as e:
        log.debug(f"AppleScript error: {e}")
        return ""


def get_now_playing() -> Optional[NowPlaying]:
    """Poll Apple Music and return current track info, or None if not playing."""
    raw = run_applescript(APPLESCRIPT_NOW_PLAYING)
    if not raw or raw == "NOT_PLAYING":
        return None
    parts = raw.split("|||")
    if len(parts) < 3:
        return None
    np = NowPlaying(
        title=parts[0].strip(),
        artist=parts[1].strip(),
        album=parts[2].strip(),
        artwork_url=parts[3].strip() if len(parts) > 3 else "",
    )
    return np


def get_artwork_bytes(np: NowPlaying) -> Optional[bytes]:
    """
    Download artwork image bytes. Tries artwork_url first; falls back to
    extracting directly from Music.app via AppleScript (works for local
    library tracks and most streaming tracks).
    """
    # Try URL first (fastest)
    if np.artwork_url:
        try:
            with urllib.request.urlopen(np.artwork_url, timeout=5) as r:
                return r.read()
        except Exception as e:
            log.debug(f"Artwork URL failed: {e}")

    # Fallback: pull raw bytes out of Music.app via AppleScript
    tmp_path = run_applescript(APPLESCRIPT_ARTWORK)
    if tmp_path:
        try:
            with open(tmp_path, "rb") as f:
                return f.read()
        except Exception as e:
            log.debug(f"Artwork file read failed: {e}")

    # Last resort: use iTunes/Apple Music API to find artwork by track+artist
    artwork_bytes = fetch_artwork_from_itunes_api(np.artist, np.title)
    return artwork_bytes


def fetch_artwork_from_itunes_api(artist: str, title: str) -> Optional[bytes]:
    """
    Queries the iTunes Search API for artwork. No auth required.
    Returns JPEG bytes of the artwork at 600x600, or None.
    """
    import json
    query = urllib.request.quote(f"{artist} {title}")
    url = f"https://itunes.apple.com/search?term={query}&media=music&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
        results = data.get("results", [])
        if not results:
            return None
        artwork_url = results[0].get("artworkUrl100", "")
        if not artwork_url:
            return None
        # Upgrade to 600x600
        artwork_url = artwork_url.replace("100x100bb", "600x600bb")
        with urllib.request.urlopen(artwork_url, timeout=5) as r:
            return r.read()
    except Exception as e:
        log.debug(f"iTunes API fallback failed: {e}")
        return None


# =============================================================================
# COLOR EXTRACTION + WHITE FILTERING
# =============================================================================

def is_white(r: int, g: int, b: int) -> bool:
    """
    Return True if this RGB color is white, near-white, off-white, or so
    pale/unsaturated that it would produce a boring/blown-out light scene.

    Uses HSV because saturation and value are much more intuitive for this
    than raw RGB distance from (255,255,255).
    """
    _, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    sat_255 = s * 255
    val_255 = v * 255
    # Low saturation alone = white/gray
    if sat_255 < WHITE_SAT_THRESHOLD:
        return True
    # High brightness + still relatively low saturation = bright near-white
    if val_255 > WHITE_VAL_THRESHOLD and sat_255 < WHITE_SAT_THRESHOLD * 1.5:
        return True
    return False


def extract_colors(image_bytes: bytes, count: int) -> list[tuple[int, int, int]]:
    """
    Extract up to `count` non-white dominant RGB colors from image bytes.

    Requests count + PALETTE_OVERSAMPLE candidates from ColorThief, strips
    anything that passes is_white(), then returns up to `count` survivors.

    Returns an empty list if all candidates are white (caller should skip
    the update entirely rather than fallback to a white scene).
    """
    buf = io.BytesIO(image_bytes)
    ct = ColorThief(buf)

    # Always request more than we need so white removal has spare candidates.
    candidates_needed = count + PALETTE_OVERSAMPLE

    if candidates_needed == 1:
        raw = [ct.get_color(quality=1)]
    else:
        raw = ct.get_palette(color_count=candidates_needed, quality=1)

    log.debug(f"  Raw palette ({len(raw)} candidates): {raw}")

    filtered = [c for c in raw if not is_white(*c)]

    log.debug(f"  After white removal ({len(filtered)} survivors): {filtered}")

    return filtered[:count]


def rgb_to_hsbk(r: int, g: int, b: int, brightness_scale: float = BRIGHTNESS) -> tuple:
    """
    Convert (R, G, B) 0–255 to LIFX HSBK tuple:
    hue [0–65535], saturation [0–65535], brightness [0–65535], kelvin [2500–9000]

    brightness_scale is the pre-computed per-bulb brightness value (already
    accounts for BRIGHTNESS and BRIGHTNESS_DYNAMIC_RANGE). Callers compute
    this via compute_brightness_scales() before calling rgb_to_hsbk.
    """
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    h, s, _ = colorsys.rgb_to_hsv(rf, gf, bf)  # v unused; caller controls brightness

    hue = int(h * 65535)
    saturation = int(s * 65535)
    brightness = int(brightness_scale * 65535)
    kelvin = NEUTRAL_KELVIN

    return (hue, saturation, brightness, kelvin)


def compute_brightness_scales(rgb_colors: list[tuple[int, int, int]]) -> list[float]:
    """
    Given a list of RGB colors, return a per-color brightness scale that:
      1. Preserves relative brightness differences from the artwork (the HSV
         value of each color reflects how light/dark it is in the image).
      2. Scales the spread of those differences by BRIGHTNESS_DYNAMIC_RANGE.
      3. Clamps the brightest color to BRIGHTNESS and the dimmest to
         at least BRIGHTNESS_FLOOR.

    With BRIGHTNESS_DYNAMIC_RANGE = 0.0, all outputs equal BRIGHTNESS.
    With BRIGHTNESS_DYNAMIC_RANGE = 1.0, the natural artwork spread is kept.
    With BRIGHTNESS_DYNAMIC_RANGE > 1.0, the spread is exaggerated.
    """
    if not rgb_colors:
        return []

    # Extract raw HSV values (0.0–1.0) from the artwork colors
    raw_values = []
    for r, g, b in rgb_colors:
        _, _, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        raw_values.append(v)

    if len(raw_values) == 1 or BRIGHTNESS_DYNAMIC_RANGE == 0.0:
        # Flat mode: all bulbs at BRIGHTNESS
        return [BRIGHTNESS] * len(rgb_colors)

    mean_v = sum(raw_values) / len(raw_values)

    scales = []
    for v in raw_values:
        # Deviation from mean, scaled by dynamic range, re-centered on BRIGHTNESS
        deviation = (v - mean_v) * BRIGHTNESS_DYNAMIC_RANGE
        scale = BRIGHTNESS + deviation
        # Hard clamp: never exceed 1.0 or drop below BRIGHTNESS_FLOOR
        scale = max(BRIGHTNESS_FLOOR, min(1.0, scale))
        scales.append(scale)

    return scales


# =============================================================================
# LIFX CONTROL
# =============================================================================

def discover_lights() -> list:
    """
    Discover LIFX lights on the local LAN.
    Filters to GROUP_NAME if USE_GROUP = True, otherwise returns all lights.
    """
    log.info("Discovering LIFX lights on LAN…")
    lan = LifxLAN()
    all_lights = lan.get_lights()

    if not all_lights:
        log.warning("No LIFX lights found. Check Wi-Fi and that lights are on.")
        return []

    log.info(f"Discovered {len(all_lights)} total LIFX light(s) on LAN")

    if USE_GROUP:
        lights = [l for l in all_lights if l.get_group() == GROUP_NAME]
        if not lights:
            log.error(f"No lights found in group '{GROUP_NAME}'. Check group name in LIFX app.")
        else:
            log.info(f"Filtered to group '{GROUP_NAME}': {len(lights)} light(s)")
            for l in lights:
                log.info(f"  • {l.get_label()} @ {l.get_ip_addr()}")
    else:
        lights = all_lights
        log.info("USE_GROUP = False — controlling all discovered lights")
        for l in lights:
            log.info(f"  • {l.get_label()} @ {l.get_ip_addr()}")

    return lights


def push_colors_to_lights(lights: list, colors_hsbk: list[tuple]) -> None:
    """
    Assign colors to lights. If more lights than colors, cycle through colors.
    All transitions are smooth (TRANSITION_MS).
    """
    if not lights or not colors_hsbk:
        return

    for i, light in enumerate(lights):
        color = colors_hsbk[i % len(colors_hsbk)]
        try:
            light.set_color(color, duration=TRANSITION_MS, rapid=True)
        except Exception as e:
            log.warning(f"Failed to set color on {light.get_label()}: {e}")


# =============================================================================
# MAIN LOOP
# =============================================================================

def main():
    log.info("=" * 60)
    log.info("  LIFX Music Immersion Daemon")
    log.info("  Watching Apple Music → painting your living room")
    log.info("=" * 60)

    lights = discover_lights()
    if not lights:
        log.error("No lights to control. Exiting.")
        return

    last_track_id = None

    while True:
        try:
            np = get_now_playing()

            if np is None:
                log.debug("Apple Music not playing.")
                time.sleep(POLL_INTERVAL)
                continue

            track_id = np.track_id()

            if track_id == last_track_id:
                # Same track — nothing to do
                time.sleep(POLL_INTERVAL)
                continue

            # Track changed!
            log.info(f"♪  {np.artist} — {np.title} [{np.album}]")
            last_track_id = track_id

            # Fetch artwork
            artwork_bytes = get_artwork_bytes(np)
            if not artwork_bytes:
                log.warning("Could not retrieve artwork; skipping color update.")
                time.sleep(POLL_INTERVAL)
                continue

            # Determine how many distinct colors to extract
            num = 1 if SINGLE_COLOR else min(NUM_COLORS, len(lights))
            rgb_colors = extract_colors(artwork_bytes, count=num)

            if not rgb_colors:
                log.info("  → Artwork is majority white — skipping light update")
                time.sleep(POLL_INTERVAL)
                continue

            # Compute per-color brightness scales (respects BRIGHTNESS_DYNAMIC_RANGE)
            brightness_scales = compute_brightness_scales(rgb_colors)
            hsbk_colors = [
                rgb_to_hsbk(r, g, b, brightness_scale=bs)
                for (r, g, b), bs in zip(rgb_colors, brightness_scales)
            ]

            log.debug(f"  Colors (RGB): {rgb_colors}")
            log.debug(f"  Brightness scales: {[f'{s:.2f}' for s in brightness_scales]}")
            log.debug(f"  Colors (HSBK): {hsbk_colors}")

            # Push to lights
            push_colors_to_lights(lights, hsbk_colors)
            log.info(f"  → Pushed {len(hsbk_colors)} color(s) to {len(lights)} light(s)")

        except KeyboardInterrupt:
            log.info("\nStopped by user.")
            break
        except Exception as e:
            log.error(f"Unexpected error: {e}", exc_info=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
