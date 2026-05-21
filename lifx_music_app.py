#!/usr/bin/env python3
# =============================================================================
# lifx_music_app.py
# Author: Andrew England / andrewengland19
# Created: 2026-05-16
# Last Updated: 2026-05-16 14:00:00
#
# macOS menu bar app for LIFX music immersion lighting.
# Wraps the daemon logic in a rumps menu bar icon with a tkinter settings
# panel. All sliders write to a live Config object — no restart needed.
#
# Dependencies:
#   pip install rumps colorthief lifxlan Pillow
#   tkinter ships with the python.org macOS installer; if missing:
#   brew install python-tk
#
# Run:
#   python3 lifx_music_app.py
# =============================================================================

import subprocess
import threading
import time
import io
import json
import os
import urllib.request
import colorsys
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    import rumps
    from colorthief import ColorThief
    from lifxlan import LifxLAN
    import tkinter as tk
    from tkinter import ttk
except ImportError as e:
    raise ImportError(
        f"Missing dependency: {e}\n"
        "Run: pip install rumps colorthief lifxlan Pillow\n"
        "If tkinter is missing: brew install python-tk\n"
    )

# =============================================================================
# CONFIG DATACLASS — single source of truth, shared between threads
# =============================================================================

CONFIG_PATH = os.path.expanduser("~/.lifx_music_config.json")

@dataclass
class Config:
    # Daemon behaviour
    poll_interval: float = 2.0
    transition_ms: int = 1500

    # Group targeting
    use_group: bool = True
    group_name: str = "Living Room"

    # Color mode
    single_color: bool = False
    num_colors: int = 3

    # Brightness
    brightness: float = 0.75
    brightness_dynamic_range: float = 1.0
    brightness_floor: float = 0.25

    # White rejection
    white_sat_threshold: int = 45
    white_val_threshold: int = 215
    palette_oversample: int = 6

    # Kelvin fallback
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


# Global live config — daemon reads from this every cycle
cfg = Config.load()

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lifx-music")

# =============================================================================
# APPLESCRIPT BRIDGE
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
    import json as _json
    query = urllib.request.quote(f"{artist} {title}")
    url = f"https://itunes.apple.com/search?term={query}&media=music&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = _json.loads(r.read())
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

# =============================================================================
# COLOR EXTRACTION + WHITE FILTERING
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

# =============================================================================
# LIFX CONTROL
# =============================================================================

_lights_cache: list = []
_lights_lock = threading.Lock()


def discover_lights() -> list:
    log.info("Discovering LIFX lights…")
    try:
        lan = LifxLAN()
        all_lights = lan.get_lights()
    except Exception as e:
        log.error(f"LAN discovery failed: {e}")
        return []

    if not all_lights:
        log.warning("No LIFX lights found.")
        return []

    if cfg.use_group:
        lights = [l for l in all_lights if l.get_group() == cfg.group_name]
        log.info(f"Group '{cfg.group_name}': {len(lights)}/{len(all_lights)} light(s)")
    else:
        lights = all_lights
        log.info(f"All lights: {len(lights)}")

    for l in lights:
        log.info(f"  • {l.get_label()} @ {l.get_ip_addr()}")
    return lights


def push_colors(lights: list, colors_hsbk: list) -> None:
    for i, light in enumerate(lights):
        color = colors_hsbk[i % len(colors_hsbk)]
        try:
            light.set_color(color, duration=cfg.transition_ms, rapid=True)
        except Exception as e:
            log.warning(f"Failed to set {light.get_label()}: {e}")

# =============================================================================
# DAEMON THREAD
# =============================================================================

class DaemonThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.running = False
        self.last_track_id: Optional[str] = None
        self.current_track: str = "—"
        self.status: str = "Stopped"

    def stop(self):
        self._stop_event.set()
        self.running = False
        self.status = "Stopped"

    def run(self):
        self.running = True
        self.status = "Discovering lights…"
        with _lights_lock:
            lights = discover_lights()

        if not lights:
            self.status = "No lights found"
            self.running = False
            return

        self.status = f"Active — {len(lights)} light(s)"

        while not self._stop_event.is_set():
            try:
                np = get_now_playing()

                if np is None:
                    self._stop_event.wait(cfg.poll_interval)
                    continue

                track_id = np.track_id()
                self.current_track = f"{np.artist} — {np.title}"

                if track_id != self.last_track_id:
                    self.last_track_id = track_id
                    log.info(f"♪  {self.current_track}")

                    artwork = get_artwork_bytes(np)
                    if not artwork:
                        self._stop_event.wait(cfg.poll_interval)
                        continue

                    num = 1 if cfg.single_color else min(cfg.num_colors, len(lights))
                    rgb_colors = extract_colors(artwork, count=num)

                    if not rgb_colors:
                        log.info("  → Majority white artwork — skipping")
                        self._stop_event.wait(cfg.poll_interval)
                        continue

                    scales = compute_brightness_scales(rgb_colors)
                    hsbk = [rgb_to_hsbk(r, g, b, bs) for (r, g, b), bs in zip(rgb_colors, scales)]
                    push_colors(lights, hsbk)
                    log.info(f"  → {len(hsbk)} color(s) pushed")

            except Exception as e:
                log.error(f"Daemon error: {e}", exc_info=True)

            self._stop_event.wait(cfg.poll_interval)

        self.status = "Stopped"
        self.running = False


# Singleton daemon reference
_daemon: Optional[DaemonThread] = None

# =============================================================================
# SETTINGS WINDOW (tkinter)
# =============================================================================

_settings_win = None


def open_settings():
    global _settings_win

    # Bring existing window to front if open
    if _settings_win and _settings_win.winfo_exists():
        _settings_win.lift()
        _settings_win.focus_force()
        return

    win = tk.Toplevel()
    win.title("LIFX Music — Settings")
    win.resizable(False, False)
    win.configure(bg="#1a1a1a")
    _settings_win = win

    # ── Styles ────────────────────────────────────────────────────────────────
    BG = "#1a1a1a"
    PANEL = "#242424"
    ACCENT = "#c17aff"
    FG = "#e8e8e8"
    FG_DIM = "#888888"
    FONT_HEAD = ("SF Pro Display", 11, "bold")
    FONT_LABEL = ("SF Pro Text", 10)
    FONT_VALUE = ("SF Mono", 10)
    FONT_SECTION = ("SF Pro Display", 9, "bold")

    win.configure(bg=BG)

    def section(parent, text):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(f, text=text.upper(), fg=ACCENT, bg=BG,
                 font=FONT_SECTION).pack(anchor="w")
        sep = tk.Frame(parent, bg="#333", height=1)
        sep.pack(fill="x", padx=20, pady=(0, 8))

    def row(parent, label, widget_factory, help_text=None):
        """Label on left, widget on right, optional help text below."""
        f = tk.Frame(parent, bg=PANEL)
        f.pack(fill="x", padx=20, pady=2)
        f.columnconfigure(1, weight=1)

        tk.Label(f, text=label, fg=FG, bg=PANEL,
                 font=FONT_LABEL, width=24, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(12, 4), pady=(8, 0))

        widget = widget_factory(f)
        widget.grid(row=0, column=1, sticky="ew", padx=(4, 12), pady=(8, 0))

        if help_text:
            tk.Label(f, text=help_text, fg=FG_DIM, bg=PANEL,
                     font=("SF Pro Text", 9), anchor="w").grid(
                row=1, column=0, columnspan=2, sticky="w",
                padx=12, pady=(0, 8))
        else:
            tk.Frame(f, bg=PANEL, height=8).grid(row=1, column=0)

        return f

    def slider_row(parent, label, var, from_, to, resolution, fmt="{:.2f}", help_text=None):
        val_label = None

        def make_widget(f):
            nonlocal val_label
            inner = tk.Frame(f, bg=PANEL)
            s = tk.Scale(inner, variable=var, from_=from_, to=to,
                         resolution=resolution, orient="horizontal",
                         bg=PANEL, fg=FG, troughcolor="#333",
                         highlightthickness=0, bd=0,
                         activebackground=ACCENT, showvalue=False,
                         length=200)
            s.pack(side="left")
            val_label = tk.Label(inner, textvariable=tk.StringVar(),
                                 fg=ACCENT, bg=PANEL, font=FONT_VALUE, width=6)
            val_label.pack(side="left", padx=(6, 0))

            def update_label(*_):
                val_label.config(text=fmt.format(var.get()))
            var.trace_add("write", update_label)
            update_label()
            return inner

        return row(parent, label, make_widget, help_text)

    def toggle_row(parent, label, var, help_text=None):
        def make_widget(f):
            cb = tk.Checkbutton(f, variable=var, bg=PANEL,
                                activebackground=PANEL,
                                selectcolor="#333",
                                fg=FG, font=FONT_LABEL,
                                highlightthickness=0)
            return cb
        return row(parent, label, make_widget, help_text)

    def entry_row(parent, label, var, help_text=None):
        def make_widget(f):
            e = tk.Entry(f, textvariable=var, bg="#333", fg=FG,
                         insertbackground=FG, font=FONT_VALUE,
                         relief="flat", bd=4)
            return e
        return row(parent, label, make_widget, help_text)

    # ── Title bar ─────────────────────────────────────────────────────────────
    title_bar = tk.Frame(win, bg=BG)
    title_bar.pack(fill="x", padx=20, pady=(16, 4))
    tk.Label(title_bar, text="LIFX Music", fg=FG, bg=BG,
             font=("SF Pro Display", 16, "bold")).pack(side="left")
    tk.Label(title_bar, text="Settings", fg=FG_DIM, bg=BG,
             font=("SF Pro Display", 16)).pack(side="left", padx=(6, 0))

    canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
    scrollbar = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    content = tk.Frame(canvas, bg=BG)
    canvas_win = canvas.create_window((0, 0), window=content, anchor="nw")

    def on_resize(event):
        canvas.itemconfig(canvas_win, width=event.width)
    canvas.bind("<Configure>", on_resize)

    def on_frame_configure(event):
        canvas.configure(scrollregion=canvas.bbox("all"))
    content.bind("<Configure>", on_frame_configure)

    # ── Tk variables — initialised from live cfg ───────────────────────────────
    v_group_name       = tk.StringVar(value=cfg.group_name)
    v_use_group        = tk.BooleanVar(value=cfg.use_group)
    v_single_color     = tk.BooleanVar(value=cfg.single_color)
    v_num_colors       = tk.IntVar(value=cfg.num_colors)
    v_poll_interval    = tk.DoubleVar(value=cfg.poll_interval)
    v_transition_ms    = tk.IntVar(value=cfg.transition_ms)
    v_brightness       = tk.DoubleVar(value=cfg.brightness)
    v_dynamic_range    = tk.DoubleVar(value=cfg.brightness_dynamic_range)
    v_brightness_floor = tk.DoubleVar(value=cfg.brightness_floor)
    v_white_sat        = tk.IntVar(value=cfg.white_sat_threshold)
    v_white_val        = tk.IntVar(value=cfg.white_val_threshold)

    # ── Section: Targeting ────────────────────────────────────────────────────
    section(content, "Light Targeting")
    p1 = tk.Frame(content, bg=PANEL, relief="flat")
    p1.pack(fill="x", padx=20, pady=0)

    toggle_row(p1, "Use LIFX group", v_use_group,
               "Off = control all lights on LAN")
    entry_row(p1, "Group name", v_group_name,
              "Must match exactly in the LIFX app")

    # ── Section: Color Mode ───────────────────────────────────────────────────
    section(content, "Color Mode")
    p2 = tk.Frame(content, bg=PANEL, relief="flat")
    p2.pack(fill="x", padx=20, pady=0)

    toggle_row(p2, "Single color mode", v_single_color,
               "All bulbs same color instead of palette")
    slider_row(p2, "Palette colors (multi)", v_num_colors,
               1, 6, 1, fmt="{:.0f}",
               help_text="Ignored when single color is on")

    # ── Section: Brightness ───────────────────────────────────────────────────
    section(content, "Brightness")
    p3 = tk.Frame(content, bg=PANEL, relief="flat")
    p3.pack(fill="x", padx=20, pady=0)

    slider_row(p3, "Master brightness", v_brightness,
               0.1, 1.0, 0.01, fmt="{:.2f}",
               help_text="Ceiling — brightest bulb hits this value")
    slider_row(p3, "Dynamic range", v_dynamic_range,
               0.0, 2.0, 0.05, fmt="{:.2f}",
               help_text="0 = flat, 1 = natural, 2 = dramatic contrast")
    slider_row(p3, "Brightness floor", v_brightness_floor,
               0.0, 1.0, 0.01, fmt="{:.2f}",
               help_text="Dimmest any bulb will go")

    # ── Section: Timing ───────────────────────────────────────────────────────
    section(content, "Timing")
    p4 = tk.Frame(content, bg=PANEL, relief="flat")
    p4.pack(fill="x", padx=20, pady=0)

    slider_row(p4, "Transition (ms)", v_transition_ms,
               200, 5000, 50, fmt="{:.0f}",
               help_text="Fade duration per color change")
    slider_row(p4, "Poll interval (s)", v_poll_interval,
               0.5, 10.0, 0.5, fmt="{:.1f}",
               help_text="How often to check Apple Music")

    # ── Section: White Rejection ──────────────────────────────────────────────
    section(content, "White Rejection")
    p5 = tk.Frame(content, bg=PANEL, relief="flat")
    p5.pack(fill="x", padx=20, pady=0)

    slider_row(p5, "Sat. threshold (0–255)", v_white_sat,
               10, 120, 1, fmt="{:.0f}",
               help_text="Colors below this saturation are treated as white")
    slider_row(p5, "Val. threshold (0–255)", v_white_val,
               150, 255, 1, fmt="{:.0f}",
               help_text="Bright+low-sat colors above this are also white")

    # ── Apply / Save buttons ──────────────────────────────────────────────────
    btn_frame = tk.Frame(content, bg=BG)
    btn_frame.pack(fill="x", padx=20, pady=20)

    def apply_settings():
        """Write Tk variables into live cfg — takes effect next daemon cycle."""
        cfg.use_group              = v_use_group.get()
        cfg.group_name             = v_group_name.get().strip()
        cfg.single_color           = v_single_color.get()
        cfg.num_colors             = int(v_num_colors.get())
        cfg.brightness             = round(v_brightness.get(), 3)
        cfg.brightness_dynamic_range = round(v_dynamic_range.get(), 3)
        cfg.brightness_floor       = round(v_brightness_floor.get(), 3)
        cfg.transition_ms          = int(v_transition_ms.get())
        cfg.poll_interval          = round(v_poll_interval.get(), 1)
        cfg.white_sat_threshold    = int(v_white_sat.get())
        cfg.white_val_threshold    = int(v_white_val.get())
        log.info("Settings applied live")

    def save_settings():
        apply_settings()
        cfg.save()
        log.info(f"Settings saved → {CONFIG_PATH}")

    tk.Button(btn_frame, text="Apply", command=apply_settings,
              bg=ACCENT, fg="#000", font=FONT_HEAD,
              relief="flat", padx=18, pady=6,
              activebackground="#d49aff", cursor="hand2").pack(side="left")

    tk.Button(btn_frame, text="Save to disk", command=save_settings,
              bg="#333", fg=FG, font=FONT_LABEL,
              relief="flat", padx=18, pady=6,
              activebackground="#444", cursor="hand2").pack(side="left", padx=(10, 0))

    tk.Label(btn_frame, text="Apply = live, Save = persists across restarts",
             fg=FG_DIM, bg=BG, font=("SF Pro Text", 9)).pack(
        side="left", padx=(14, 0))

    win.update_idletasks()
    win.geometry(f"520x{min(win.winfo_reqheight(), 700)}")
    win.lift()


# =============================================================================
# MENU BAR APP
# =============================================================================

class LIFXMusicApp(rumps.App):

    def __init__(self):
        super().__init__(
            name="LIFX Music",
            title=None,
            icon=None,          # set to a .png path for a custom icon
            template=True,      # renders as monochrome in the menu bar
            quit_button=None,   # we'll add our own
        )

        # Use a music note as the menu bar symbol (works without an icon file)
        self.title = "♫"

        self._daemon: Optional[DaemonThread] = None

        # ── Menu items ────────────────────────────────────────────────────────
        self.track_item    = rumps.MenuItem("—", callback=None)
        self.toggle_item   = rumps.MenuItem("▶  Start", callback=self.toggle_daemon)
        self.settings_item = rumps.MenuItem("Settings…", callback=self.open_settings_menu)
        self.music_item    = rumps.MenuItem("Open Apple Music", callback=self.open_apple_music)
        self.quit_item     = rumps.MenuItem("Quit", callback=self.quit_app)

        self.track_item.set_callback(None)  # not clickable

        self.menu = [
            self.track_item,
            rumps.separator,
            self.toggle_item,
            rumps.separator,
            self.settings_item,
            self.music_item,
            rumps.separator,
            self.quit_item,
        ]

        # Refresh menu bar title/status every 3s
        self._timer = rumps.Timer(self._refresh_status, 3)
        self._timer.start()

    # ── Daemon control ────────────────────────────────────────────────────────

    def toggle_daemon(self, _):
        if self._daemon and self._daemon.running:
            self._daemon.stop()
            self._daemon = None
            self.toggle_item.title = "▶  Start"
            self.title = "♫"
        else:
            self._daemon = DaemonThread()
            self._daemon.start()
            self.toggle_item.title = "⏹  Stop"
            self.title = "♫●"

    def _refresh_status(self, _):
        if self._daemon and self._daemon.running:
            track = self._daemon.current_track
            # Truncate long track names so the menu bar stays tidy
            display = track if len(track) <= 35 else track[:32] + "…"
            self.track_item.title = f"♪  {display}" if track != "—" else "Listening…"
        else:
            self.track_item.title = "—"

    # ── Settings ──────────────────────────────────────────────────────────────

    def open_settings_menu(self, _):
        # tkinter must run on the main thread; use rumps.Window as a bridge
        # to keep the event loop happy, then open tk in a thread
        t = threading.Thread(target=self._open_tk_settings, daemon=True)
        t.start()

    def _open_tk_settings(self):
        root = tk.Tk()
        root.withdraw()          # hide the blank root window
        open_settings()
        root.mainloop()

    # ── Apple Music ───────────────────────────────────────────────────────────

    def open_apple_music(self, _):
        subprocess.Popen(["open", "-a", "Music"])

    # ── Quit ─────────────────────────────────────────────────────────────────

    def quit_app(self, _):
        if self._daemon:
            self._daemon.stop()
        cfg.save()
        rumps.quit_application()


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    LIFXMusicApp().run()
