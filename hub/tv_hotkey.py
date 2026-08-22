#!/usr/bin/env python3
# =============================================================================
# hub/tv_hotkey.py  —  global hotkey daemon
#
# Registers Control+Shift+Enter system-wide and fires `tv ok` (select button)
# when pressed — handy for skipping theme songs without touching the remote.
#
# Run at login via LaunchAgent:
#   ~/Library/LaunchAgents/com.chroma.tv-hotkey.plist
#
# First-time setup: the process needs Accessibility access.
#   System Settings → Privacy & Security → Accessibility
#   Add: /Users/andy/Code/Chroma/hub/.venv/bin/python3
# =============================================================================

import subprocess
import sys
from pynput import keyboard

TV = "/usr/local/bin/tv"


def on_activate():
    subprocess.Popen([TV, "ok"])


print("tv-hotkey: listening for Ctrl+Shift+Enter", flush=True)

try:
    with keyboard.GlobalHotKeys({"<ctrl>+<shift>+<enter>": on_activate}) as h:
        h.join()
except Exception as e:
    print(f"tv-hotkey: {e}", file=sys.stderr)
    sys.exit(1)
