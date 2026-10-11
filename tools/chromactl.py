#!/usr/bin/env python3
# =============================================================================
# tools/chromactl.py — debug CLI over chromad's Unix socket.
#
#   chromactl.py state                         full state (pretty JSON)
#   chromactl.py watch [--count N] [--raw]     subscribe: one line per event
#   chromactl.py intent '{"intent":"fader","ch":3,"v":0.6}'
#   chromactl.py intent fader ch=3 v=0.6       (key=value form; numbers/bools parsed)
#   chromactl.py op set_art_mode mode=paint    any op, args as JSON or key=value
#
# Socket: --socket, else $CHROMA_HOME/chromad.sock, else ~/.chroma/chromad.sock.
# Standard library only, so it runs under any python3.
# =============================================================================

import argparse
import json
import os
import socket
import sys
import time


def default_socket() -> str:
    home = os.environ.get("CHROMA_HOME") or "~/.chroma"
    return os.path.join(os.path.abspath(os.path.expanduser(home)), "chromad.sock")


def parse_value(v: str):
    if v in ("true", "false"):
        return v == "true"
    if v == "null":
        return None
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return v


def parse_kv(items: list) -> dict:
    out = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"chromactl: expected key=value, got {it!r}")
        k, v = it.split("=", 1)
        out[k] = parse_value(v)
    return out


class Conn:
    def __init__(self, path: str, timeout: float = 30.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(path)
        except OSError as e:
            raise SystemExit(f"chromactl: cannot connect to {path}: {e} (is chromad running?)")
        self.buf = b""
        self.next_id = 1

    def send(self, obj: dict) -> int:
        rid = self.next_id
        self.next_id += 1
        self.sock.sendall(json.dumps({"id": rid, **obj}).encode() + b"\n")
        return rid

    def read(self) -> dict:
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise SystemExit("chromactl: chromad closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def request(self, obj: dict) -> dict:
        rid = self.send(obj)
        while True:
            msg = self.read()
            if msg.get("id") == rid:
                return msg


def summarize(ev: dict) -> str:
    if ev.get("ev") == "patch":
        return "patch " + json.dumps(ev["patch"], separators=(",", ":"))
    if ev.get("ev") == "state":
        now = ev["state"]["now"]
        return f"state  {now['artist']} — {now['title']}  pos={now['pos_ms']}  who={ev['state']['who']}"
    if ev.get("ev") == "toast":
        return f"toast  [{ev['level']}] {ev['text']}"
    return json.dumps(ev)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="chromactl")
    ap.add_argument("--socket", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("state")
    w = sub.add_parser("watch")
    w.add_argument("--count", type=int, default=0, help="exit after N events (0 = forever)")
    w.add_argument("--raw", action="store_true", help="print raw JSON lines")
    i = sub.add_parser("intent")
    i.add_argument("args", nargs="+")
    o = sub.add_parser("op")
    o.add_argument("name")
    o.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    conn = Conn(a.socket or default_socket())

    if a.cmd == "state":
        r = conn.request({"op": "get_state"})
        if not r.get("ok"):
            print(f"chromactl: {r.get('error')}", file=sys.stderr)
            return 1
        print(json.dumps(r["state"], indent=2, ensure_ascii=False))
        return 0

    if a.cmd == "watch":
        conn.sock.settimeout(None)
        r = conn.request({"op": "subscribe"})
        if not r.get("ok"):
            print(f"chromactl: {r.get('error')}", file=sys.stderr)
            return 1
        n = 0
        try:
            while True:
                ev = conn.read()
                t = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
                print(json.dumps(ev, ensure_ascii=False) if a.raw else f"{t}  {summarize(ev)}", flush=True)
                n += 1
                if a.count and n >= a.count:
                    return 0
        except KeyboardInterrupt:
            return 0

    if a.cmd == "intent":
        if len(a.args) == 1 and a.args[0].lstrip().startswith("{"):
            intent = json.loads(a.args[0])
        else:
            intent = {"intent": a.args[0], **parse_kv(a.args[1:])}
        r = conn.request({"op": "intent", "intent": intent})
    else:
        if len(a.args) == 1 and a.args[0].lstrip().startswith("{"):
            args = json.loads(a.args[0])
        else:
            args = parse_kv(a.args)
        r = conn.request({"op": a.name, **args})
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
