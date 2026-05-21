#!/usr/bin/env python3
# =============================================================================
# lifx_music_app.py — CHROMA Python sidecar
# Author: Andrew England / andrewengland19
#
# Headless daemon that bridges Apple Music + LIFX bulbs.
# Communicates with the Electron host over stdio:
#   - Emits newline-delimited JSON events on stdout
#   - Reads newline-delimited JSON commands on stdin
#
# Dependencies:
#   pip install colorthief lifxlan Pillow
# =============================================================================

import sys
import subprocess
import threading
import time
import io
import json
import os
import tempfile
import urllib.request
import urllib.parse
import colorsys
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, List, Tuple

try:
    from colorthief import ColorThief
    from lifxlan import LifxLAN
except ImportError as e:
    sys.stderr.write(
        f"Missing dependency: {e}\n"
        "Run: pip install colorthief lifxlan Pillow\n"
    )
    sys.exit(1)

# =============================================================================
# PATHS
# =============================================================================

CHROMA_DIR = os.path.expanduser("~/.chroma")
os.makedirs(CHROMA_DIR, exist_ok=True)

CONFIG_PATH = os.path.join(CHROMA_DIR, "config.json")
ARTWORK_PATH = os.path.join(CHROMA_DIR, "current_artwork.jpg")
COLORS_PATH = os.path.join(CHROMA_DIR, "current_colors.json")


def export_colors_for_spectrum(hsbk_colors: list, track: Optional[str]) -> None:
    """Atomically write current palette to ~/.chroma/current_colors.json.

    Consumed by SPECTRUM's BEAT mode on its 60Hz hot path — must never
    leave a partial file behind. Failures are swallowed; this is additive.
    """
    try:
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "track": track,
            "colors": [
                {"h": int(h), "s": int(s), "b": int(b), "k": int(k)}
                for (h, s, b, k) in hsbk_colors
            ],
        }
        fd, tmp_path = tempfile.mkstemp(
            prefix=".current_colors.", suffix=".tmp", dir=CHROMA_DIR
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_path, COLORS_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        try:
            sys.stderr.write(f"spectrum export failed: {e}\n")
        except Exception:
            pass

# =============================================================================
# CONFIG
# =============================================================================

@dataclass
class Config:
    poll_interval: float = 2.0
    transition_ms: int = 1500

    use_group: bool = True
    group_name: str = "Living Room"

    single_color: bool = False
    num_colors: int = 3

    brightness: float = 0.75
    brightness_dynamic_range: float = 1.0
    brightness_floor: float = 0.25

    white_sat_threshold: int = 45
    white_val_threshold: int = 215
    palette_oversample: int = 6

    neutral_kelvin: int = 3500

    def save(self):
        with open(CONFIG_PATH, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls) -> "Config":
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH) as f:
                    data = json.load(f)
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception:
                pass
        return cls()


cfg = Config.load()

# Pin override colors (RGB tuples). When non-empty, ColorThief is bypassed.
pin_colors: List[Tuple[int, int, int]] = []
pin_lock = threading.Lock()

# =============================================================================
# IPC
# =============================================================================

_emit_lock = threading.Lock()


def emit(event: str, **fields):
    """Emit a JSON event line to stdout."""
    payload = {"event": event, **fields}
    with _emit_lock:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()


# Logging goes to stderr so it never collides with the IPC channel
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chroma")

# =============================================================================
# APPLESCRIPT
# =============================================================================

APPLESCRIPT_NOW_PLAYING = """
tell application "Music"
    if player state is playing then
        set t to name of current track
        set ar to artist of current track
        set al to album of current track
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
tell application "Music"
    if player state is playing then
        set tmpPath to POSIX path of (path to temporary items folder) & "chroma_artwork.jpg"
        try
            set theTrack to current track
            set theArtwork to artwork 1 of theTrack
            set picData to raw data of theArtwork
            set fileRef to open for access POSIX file tmpPath with write permission
            set eof of fileRef to 0
            write picData to fileRef
            close access fileRef
            return tmpPath
        on error
            return ""
        end try
    else
        return ""
    end if
end tell
"""


@dataclass
class NowPlaying:
    title: str = ""
    artist: str = ""
    album: str = ""
    artwork_url: str = ""

    def track_id(self) -> str:
        return f"{self.artist}|||{self.title}"


def run_applescript(script: str) -> str:
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception as e:
        log.debug(f"AppleScript error: {e}")
        return ""


def get_now_playing() -> Optional[NowPlaying]:
    raw = run_applescript(APPLESCRIPT_NOW_PLAYING)
    if not raw or raw == "NOT_PLAYING":
        return None
    parts = raw.split("|||")
    if len(parts) < 3:
        return None
    return NowPlaying(
        title=parts[0].strip(),
        artist=parts[1].strip(),
        album=parts[2].strip(),
        artwork_url=parts[3].strip() if len(parts) > 3 else "",
    )

# =============================================================================
# ARTWORK
# =============================================================================

def get_artwork_bytes(np: NowPlaying) -> Optional[bytes]:
    if np.artwork_url:
        try:
            with urllib.request.urlopen(np.artwork_url, timeout=5) as r:
                return r.read()
        except Exception:
            pass

    tmp_path = run_applescript(APPLESCRIPT_ARTWORK)
    if tmp_path:
        try:
            with open(tmp_path, "rb") as f:
                return f.read()
        except Exception:
            pass

    return fetch_artwork_from_itunes_api(np.artist, np.title)


def fetch_artwork_from_itunes_api(artist: str, title: str) -> Optional[bytes]:
    query = urllib.parse.quote(f"{artist} {title}")
    url = f"https://itunes.apple.com/search?term={query}&media=music&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
        results = data.get("results", [])
        if not results:
            return None
        aw = results[0].get("artworkUrl100", "").replace("100x100bb", "600x600bb")
        if not aw:
            return None
        with urllib.request.urlopen(aw, timeout=5) as r:
            return r.read()
    except Exception:
        return None


def write_artwork(image_bytes: bytes):
    try:
        with open(ARTWORK_PATH, "wb") as f:
            f.write(image_bytes)
    except Exception as e:
        log.warning(f"Failed to write artwork: {e}")

# =============================================================================
# COLOR
# =============================================================================

def is_white(r: int, g: int, b: int) -> bool:
    _, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    sat = s * 255
    val = v * 255
    if sat < cfg.white_sat_threshold:
        return True
    if val > cfg.white_val_threshold and sat < cfg.white_sat_threshold * 1.5:
        return True
    return False


def extract_colors(image_bytes: bytes, count: int) -> list:
    buf = io.BytesIO(image_bytes)
    ct = ColorThief(buf)
    needed = count + cfg.palette_oversample
    raw = ct.get_palette(color_count=needed, quality=1) if needed > 1 else [ct.get_color(quality=1)]
    filtered = [c for c in raw if not is_white(*c)]
    return filtered[:count]


def compute_brightness_scales(rgb_colors: list) -> list:
    if not rgb_colors:
        return []
    raw_values = [colorsys.rgb_to_hsv(r/255., g/255., b/255.)[2] for r, g, b in rgb_colors]
    if len(raw_values) == 1 or cfg.brightness_dynamic_range == 0.0:
        return [cfg.brightness] * len(rgb_colors)
    mean_v = sum(raw_values) / len(raw_values)
    scales = []
    for v in raw_values:
        dev = (v - mean_v) * cfg.brightness_dynamic_range
        scale = max(cfg.brightness_floor, min(1.0, cfg.brightness + dev))
        scales.append(scale)
    return scales


def rgb_to_hsbk(r: int, g: int, b: int, brightness_scale: float) -> tuple:
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    h, s, _ = colorsys.rgb_to_hsv(rf, gf, bf)
    return (
        int(h * 65535),
        int(s * 65535),
        int(brightness_scale * 65535),
        cfg.neutral_kelvin,
    )


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return "#{:02x}{:02x}{:02x}".format(r, g, b)

# =============================================================================
# LIFX
# =============================================================================

_lights_cache: list = []
_lights_lock = threading.Lock()


def discover_lights() -> list:
    emit("status", message="Discovering lights…")
    try:
        lan = LifxLAN()
        all_lights = lan.get_lights()
    except Exception as e:
        emit("error", message=f"LAN discovery failed: {e}")
        return []

    if not all_lights:
        emit("status", message="No LIFX lights found")
        return []

    if cfg.use_group:
        lights = [l for l in all_lights if l.get_group() == cfg.group_name]
    else:
        lights = all_lights

    emit("status", message=f"Active — {len(lights)} light(s)")
    return lights


def push_colors(lights: list, colors_hsbk: list) -> None:
    for i, light in enumerate(lights):
        color = colors_hsbk[i % len(colors_hsbk)]
        try:
            light.set_color(color, duration=cfg.transition_ms, rapid=True)
        except Exception as e:
            log.warning(f"Failed to set light: {e}")

# =============================================================================
# DAEMON
# =============================================================================

class DaemonThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.running = False
        self.last_track_id: Optional[str] = None
        self.last_pin_signature: Optional[str] = None

    def stop(self):
        self._stop_event.set()
        self.running = False

    def run(self):
        self.running = True
        with _lights_lock:
            lights = discover_lights()

        if not lights:
            self.running = False
            return

        while not self._stop_event.is_set():
            try:
                np = get_now_playing()
                if np is None:
                    self._stop_event.wait(cfg.poll_interval)
                    continue

                track_id = np.track_id()
                track_changed = track_id != self.last_track_id

                if track_changed:
                    self.last_track_id = track_id
                    artwork = get_artwork_bytes(np)
                    if artwork:
                        write_artwork(artwork)
                        self._last_artwork = artwork
                        emit("track_change",
                             artist=np.artist, title=np.title, album=np.album,
                             artwork_path=ARTWORK_PATH)
                    else:
                        emit("track_change",
                             artist=np.artist, title=np.title, album=np.album,
                             artwork_path="")
                        self._stop_event.wait(cfg.poll_interval)
                        continue

                # Determine palette: pins override ColorThief
                with pin_lock:
                    pins_snapshot = list(pin_colors)
                pin_sig = json.dumps(pins_snapshot)

                if pins_snapshot:
                    rgb_colors = pins_snapshot
                    pin_changed = pin_sig != self.last_pin_signature
                    if not (track_changed or pin_changed):
                        self._stop_event.wait(cfg.poll_interval)
                        continue
                    self.last_pin_signature = pin_sig
                else:
                    if not track_changed and self.last_pin_signature is None:
                        # Nothing to do; track unchanged & no pins
                        self._stop_event.wait(cfg.poll_interval)
                        continue
                    self.last_pin_signature = None
                    artwork = getattr(self, "_last_artwork", None)
                    if not artwork:
                        self._stop_event.wait(cfg.poll_interval)
                        continue
                    num = 1 if cfg.single_color else min(cfg.num_colors, len(lights))
                    rgb_colors = extract_colors(artwork, count=num)
                    if not rgb_colors:
                        emit("white_skip", reason="majority white artwork")
                        self._stop_event.wait(cfg.poll_interval)
                        continue

                if cfg.single_color and rgb_colors:
                    rgb_colors = rgb_colors[:1]

                scales = compute_brightness_scales(rgb_colors)
                hsbk = [rgb_to_hsbk(r, g, b, bs) for (r, g, b), bs in zip(rgb_colors, scales)]
                push_colors(lights, hsbk)
                export_colors_for_spectrum(
                    hsbk,
                    f"{np.artist} — {np.title}" if np else None,
                )
                emit("colors_pushed",
                     colors=[rgb_to_hex(*c) for c in rgb_colors],
                     brightness_scales=[round(s, 3) for s in scales])

            except Exception as e:
                emit("error", message=f"Daemon error: {e}")

            self._stop_event.wait(cfg.poll_interval)

        self.running = False


_daemon: Optional[DaemonThread] = None
_daemon_lock = threading.Lock()


def start_daemon():
    global _daemon
    with _daemon_lock:
        if _daemon and _daemon.running:
            return
        _daemon = DaemonThread()
        _daemon.start()


def stop_daemon():
    global _daemon
    with _daemon_lock:
        if _daemon:
            _daemon.stop()
            _daemon = None
    emit("status", message="Stopped")

# =============================================================================
# STDIN COMMAND LOOP
# =============================================================================

def _hex_to_rgb(s: str) -> Optional[Tuple[int, int, int]]:
    s = s.strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def handle_command(cmd: dict):
    name = cmd.get("cmd")

    if name == "set_config":
        key = cmd.get("key")
        value = cmd.get("value")
        if key in cfg.__dataclass_fields__:
            field_type = cfg.__dataclass_fields__[key].type
            try:
                if field_type is int or field_type == "int":
                    value = int(value)
                elif field_type is float or field_type == "float":
                    value = float(value)
                elif field_type is bool or field_type == "bool":
                    value = bool(value)
                setattr(cfg, key, value)
                cfg.save()
                emit("config_updated", key=key, value=value)
            except Exception as e:
                emit("error", message=f"set_config failed for {key}: {e}")
        else:
            emit("error", message=f"Unknown config key: {key}")

    elif name == "set_colors":
        hexes = cmd.get("colors", [])
        rgbs = [c for c in (_hex_to_rgb(h) for h in hexes) if c is not None]
        with pin_lock:
            pin_colors.clear()
            pin_colors.extend(rgbs)
        emit("pins_updated", colors=[rgb_to_hex(*c) for c in rgbs])

    elif name == "clear_pins":
        with pin_lock:
            pin_colors.clear()
        emit("pins_updated", colors=[])

    elif name == "start":
        start_daemon()

    elif name == "stop":
        stop_daemon()

    elif name == "get_config":
        emit("config", config=asdict(cfg))

    else:
        emit("error", message=f"Unknown command: {name}")


def stdin_loop():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            emit("error", message=f"Invalid JSON: {e}")
            continue
        try:
            handle_command(cmd)
        except Exception as e:
            emit("error", message=f"Command failed: {e}")
    # stdin closed → parent died → exit
    stop_daemon()
    sys.exit(0)

# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    emit("ready", config=asdict(cfg))
    t = threading.Thread(target=stdin_loop, daemon=False)
    t.start()
    t.join()
