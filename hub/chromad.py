#!/usr/bin/env python3
# =============================================================================
# hub/chromad.py — the CHROMA engine daemon (BUILD_ORDER §4).
#
# Runs the existing Engine (Apple TV → art → LIFX), the SongClock and the console
# behind a Unix socket ($CHROMA_HOME/chromad.sock, 0600, NDJSON — see hub/ipc.py).
# The Tauri app, tools/chromactl.py and (M7) `lite show` talk to it.
#
#   hub/.venv/bin/python hub/chromad.py                 real Apple TV + bulbs, ~/.chroma
#   hub/.venv/bin/python hub/chromad.py --fake --home /tmp/cx   no hardware, isolated state
#
# A real (non-fake) chromad refuses to start while the old hub (`lite show`,
# server.py) is running against the same home, so two engines never fight over
# the bulbs. --allow-alongside overrides that.
# =============================================================================

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys

HUB_DIR = os.path.dirname(os.path.abspath(__file__))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="chromad", description="CHROMA engine daemon")
    ap.add_argument("--home", help="state directory (default $CHROMA_HOME or ~/.chroma)")
    ap.add_argument("--fake", action="store_true", help="FakeProvider + fake bulbs: no hardware")
    ap.add_argument("--no-lifx", action="store_true", help="drive no LIFX bulbs")
    ap.add_argument("--socket", help="socket path (default <home>/chromad.sock)")
    ap.add_argument("--replay-offset-ms", type=int, default=0,
                    help="global take replay offset (ms, + = later), on top of each take's nudge")
    ap.add_argument("--board-port", type=int, default=9876,
                    help="UDP port for the physical board (listens only after board.pairing created board.key)")
    ap.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    ap.add_argument("--allow-alongside", action="store_true",
                    help="start even if the old hub (server.py) is running on this home")
    return ap.parse_args(argv)


def old_hub_pid(home: str):
    """PID of a running server.py hub using this home, else None."""
    try:
        with open(os.path.join(home, "hub.pid")) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    return pid if "server.py" in cmd else None


def blocking_hub(home: str, no_lifx: bool):
    """(pid, home) of an old hub this chromad would fight, else None. Same home: shared
    state files. The default home: the live hub, whose bulbs we'd also drive unless
    --no-lifx."""
    homes = [home]
    default = os.path.abspath(os.path.expanduser("~/.chroma"))
    if not no_lifx and default != home:
        homes.append(default)
    for h in homes:
        pid = old_hub_pid(h)
        if pid:
            return pid, h
    return None


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.home:
        os.environ["CHROMA_HOME"] = os.path.abspath(os.path.expanduser(args.home))
    if HUB_DIR not in sys.path:
        sys.path.insert(0, HUB_DIR)
    from paths import chroma_dir   # imported after CHROMA_HOME is final
    home = chroma_dir()
    os.makedirs(home, exist_ok=True)

    if not args.fake and not args.allow_alongside:
        clash = blocking_hub(home, args.no_lifx)
        if clash:
            pid, h = clash
            print(f"chromad: the old hub is running (server.py, pid {pid}) on {h}; "
                  "stop it first (lite show stop) or pass --allow-alongside", file=sys.stderr)
            return 2

    from daemon import Chromad
    logging.getLogger().setLevel(args.log_level)

    async def run():
        loop = asyncio.get_running_loop()
        d = Chromad(loop, fake=args.fake, no_lifx=args.no_lifx, socket_path=args.socket,
                    replay_offset_ms=args.replay_offset_ms, board_port=args.board_port)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, d.request_stop)
        await d.run()

    try:
        asyncio.run(run())
    except RuntimeError as e:
        print(f"chromad: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
