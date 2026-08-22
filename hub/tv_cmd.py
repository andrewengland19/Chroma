#!/usr/bin/env python3
# =============================================================================
# hub/tv_cmd.py
# Rapid-fire Apple TV CLI. Run via the `tv` shell wrapper.
#
# Usage:
#   tv now                     show what's playing
#   tv play / pause / stop     transport
#   tv toggle                  play/pause toggle
#   tv next / prev             track navigation
#   tv vol up [N]              volume up N notches (default 1)
#   tv vol down [N]            volume down N notches (default 1)
#   tv skip [N]                skip forward N seconds (default 30)
#   tv back [N]                skip backward N seconds (default 10)
#   tv up / down / left / right  d-pad
#   tv select / ok             select button
#   tv menu                    menu button
#   tv home                    home button
#   tv top                     top-menu button
#   tv wake                    wake / power on
#   tv sleep                   sleep / power off
#   tv apps                    list launchable apps
#   tv launch <name>           launch an app by name (partial, case-insensitive)
# =============================================================================

import asyncio
import sys

import pyatv
from pyatv.const import Protocol as ATVProtocol, DeviceState

# providers.py lives in the same directory (the shell wrapper cds there).
from providers import load_credentials

USAGE = """\
Usage: tv <command>

  now                    what's playing
  play / pause / stop    transport
  toggle                 play-pause toggle
  next / prev            track navigation
  vol up [N]             volume up N notches  (default 1)
  vol down [N]           volume down N notches (default 1)
  skip [N]               skip forward N seconds (default 30)
  back [N]               skip back N seconds (default 10)
  up / down / left / right / select / ok / menu / home / top
  wake / sleep           power on / off
  apps                   list launchable apps
  launch <name>          launch app by name"""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

async def _connect(stored: dict):
    loop = asyncio.get_event_loop()
    results = await pyatv.scan(loop, identifier=stored["identifier"], timeout=2)
    if not results:
        print(f"tv: '{stored.get('name', 'Apple TV')}' not found on LAN (is it awake?)")
        sys.exit(1)
    config = results[0]
    for proto_name, creds in stored["credentials"].items():
        config.set_credentials(ATVProtocol[proto_name], creds)
    return await pyatv.connect(config, loop)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_now(atv):
    p = await atv.metadata.playing()
    state = p.device_state
    icon = "▶" if state == DeviceState.Playing else ("⏸" if state == DeviceState.Paused else "○")
    title = p.title or ""
    artist = p.artist or ""
    album = p.album or ""
    if not title and not artist:
        print("○  nothing playing")
        return
    parts = [artist, title]
    label = " — ".join(x for x in parts if x)
    if album:
        label += f"  [{album}]"
    print(f"{icon}  {label}")


async def cmd_apps(atv):
    apps = await atv.apps.app_list()
    apps_sorted = sorted(apps, key=lambda a: a.name.lower())
    for i, app in enumerate(apps_sorted):
        print(f"  {i:2d}.  {app.name}  ({app.identifier})")


async def cmd_launch(atv, name: str):
    apps = await atv.apps.app_list()
    name_lower = name.lower()
    matches = [a for a in apps if name_lower in a.name.lower()]
    if not matches:
        print(f"tv: no app matching '{name}' — run 'tv apps' to list all")
        sys.exit(1)
    app = matches[0]
    print(f"launching {app.name}")
    await atv.apps.launch_app(app.identifier)


async def cmd_vol(atv, direction: str, count: int):
    if direction not in ("up", "down"):
        print(f"tv: unknown volume direction '{direction}' — use 'up' or 'down'")
        sys.exit(1)
    fn = atv.remote_control.volume_up if direction == "up" else atv.remote_control.volume_down
    for _ in range(count):
        await fn()


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

async def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        sys.exit(0)

    cmd = args[0].lower()

    # Read-only early exits that don't need the ATV connection
    # (none right now, but placeholder for future).

    stored = load_credentials()
    if not stored:
        print("tv: not paired — run:  python hub/pair_atv.py")
        sys.exit(1)

    atv = await _connect(stored)
    try:
        rc = atv.remote_control

        if cmd == "now":
            await cmd_now(atv)

        elif cmd == "play":
            await rc.play()

        elif cmd == "pause":
            await rc.pause()

        elif cmd == "stop":
            await rc.stop()

        elif cmd in ("toggle", "pp"):
            await rc.play_pause()

        elif cmd == "next":
            await rc.next()

        elif cmd in ("prev", "previous"):
            await rc.previous()

        elif cmd in ("vol", "volume"):
            direction = args[1].lower() if len(args) > 1 else ""
            count = int(args[2]) if len(args) > 2 else 1
            if not direction:
                print("tv: usage: tv vol up|down [N]")
                sys.exit(1)
            await cmd_vol(atv, direction, count)

        elif cmd == "skip":
            secs = float(args[1]) if len(args) > 1 else 30.0
            await rc.skip_forward(secs)

        elif cmd == "back":
            secs = float(args[1]) if len(args) > 1 else 10.0
            await rc.skip_backward(secs)

        elif cmd == "up":
            await rc.up()
        elif cmd == "down":
            await rc.down()
        elif cmd == "left":
            await rc.left()
        elif cmd == "right":
            await rc.right()

        elif cmd in ("select", "ok"):
            await rc.select()

        elif cmd == "menu":
            await rc.menu()

        elif cmd == "home":
            await rc.home()

        elif cmd == "top":
            await rc.top_menu()

        elif cmd == "wake":
            await atv.power.turn_on()

        elif cmd == "sleep":
            await atv.power.turn_off()

        elif cmd == "apps":
            await cmd_apps(atv)

        elif cmd == "launch":
            if len(args) < 2:
                print("tv: usage: tv launch <app name>")
                sys.exit(1)
            await cmd_launch(atv, " ".join(args[1:]))

        else:
            print(f"tv: unknown command '{cmd}'\n")
            print(USAGE)
            sys.exit(1)

    finally:
        atv.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"tv: {e}")
        sys.exit(1)
