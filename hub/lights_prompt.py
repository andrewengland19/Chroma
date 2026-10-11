#!/usr/bin/env python3
# =============================================================================
# hub/lights_prompt.py  —  Pass 5: the ⌃⇧L "describe the vibe" box.
#
# Fired by tv_hotkey.py. Native macOS dialog → lights agent (through the hub if
# it's running, else directly) → result dialog with Undo / Save / Keep that
# dismisses itself after 10s. Never blocks the hotkey daemon (runs as its own
# process).
# =============================================================================

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HUB = "http://127.0.0.1:8765"
HERE = os.path.dirname(os.path.abspath(__file__))


def osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def call(path, body):
    """POST to the hub; fall back to running the agent in-process."""
    try:
        req = urllib.request.Request(HUB + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)
    except (urllib.error.URLError, ConnectionError):
        sys.path.insert(0, HERE)
        import lights_agent as A
        try:
            if path == "/agent":
                return A.run(body["prompt"])
            if path == "/agent/undo":
                return A.undo()
            return A.t_save_scene({"name": body["name"]})
        except Exception as e:
            return {"error": str(e)}


def main():
    rc, out = osa('display dialog "Describe the lights" default answer "" '
                  'with title "Lights" buttons {"Cancel", "Set"} default button "Set" '
                  'cancel button "Cancel"')
    if rc != 0:
        return
    prompt = out.split("text returned:", 1)[-1].strip()
    if not prompt:
        return
    osa(f'display notification {q(prompt)} with title "Lights" subtitle "Setting the mood…"')
    r = call("/agent", {"prompt": prompt})
    if "error" in r:
        osa(f'display dialog {q(r["error"])} with title "Lights" buttons {{"OK"}} '
            'default button "OK" with icon caution giving up after 15')
        return
    name = r.get("suggested_name") or ""
    rc, out = osa(f'display dialog {q(r["reply"])} with title "Lights" '
                  'buttons {"Undo", "Save…", "Keep"} default button "Keep" giving up after 10')
    if "Undo" in out:
        call("/agent/undo", {})
    elif "Save" in out:
        rc, out = osa(f'display dialog "Scene name" default answer {q(name)} with title "Lights" '
                      'buttons {"Cancel", "Save"} default button "Save" cancel button "Cancel"')
        if rc == 0:
            n = out.split("text returned:", 1)[-1].strip()
            if n:
                s = call("/agent/save", {"name": n})
                msg = s.get("error") or f"Saved scene “{n}”"
                osa(f'display notification {q(msg)} with title "Lights"')


if __name__ == "__main__":
    main()
