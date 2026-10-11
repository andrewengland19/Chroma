#!/usr/bin/env python3
# =============================================================================
# hub/tv_hotkey.py  —  global hotkey daemon
#
# Registers system-wide hotkeys:
#   Control+Shift+Enter → `tv ok` (select button) — skip theme songs.
#   Control+Shift+L     → lights_prompt.py — describe the lights, agent sets them.
#
# Run at login via LaunchAgent:
#   ~/Library/LaunchAgents/com.chroma.tv-hotkey.plist
#
# First-time setup: the process needs Accessibility access.
#   System Settings → Privacy & Security → Accessibility
#   Add: /Users/andy/Code/Chroma/hub/.venv/bin/python3
# =============================================================================

import os
import subprocess
import sys
from pynput import keyboard

TV = "/usr/local/bin/tv"
LIGHTS_PROMPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lights_prompt.py")


def on_activate():
    subprocess.Popen([TV, "ok"])


def on_lights():
    subprocess.Popen([sys.executable, LIGHTS_PROMPT], start_new_session=True)


print("tv-hotkey: listening for Ctrl+Shift+Enter, Ctrl+Shift+L", flush=True)

try:
    with keyboard.GlobalHotKeys({"<ctrl>+<shift>+<enter>": on_activate,
                                "<ctrl>+<shift>+l": on_lights}) as h:
        h.join()
except Exception as e:
    print(f"tv-hotkey: {e}", file=sys.stderr)
    sys.exit(1)
