# M1: hub/ipc.py — merge-patch helpers and the Unix-socket server (temp socket).

import asyncio
import json
import os
import stat
import tempfile
import time

import pytest

from ipc import IpcError, IpcServer, apply_merge_patch, merge_patch_diff


# ---- merge patches -------------------------------------------------------------

@pytest.mark.parametrize("old,new,patch", [
    ({"a": 1}, {"a": 1}, None),
    ({"a": 1}, {"a": 2}, {"a": 2}),
    ({"a": {"b": 1, "c": 2}}, {"a": {"b": 1, "c": 3}}, {"a": {"c": 3}}),
    ({"a": [1, 2]}, {"a": [1, 3]}, {"a": [1, 3]}),
    ({"a": 1, "b": 2}, {"a": 1}, {"b": None}),
    ({"a": None}, {"a": {"x": 1}}, {"a": {"x": 1}}),
    ({"a": {"x": 1}}, {"a": None}, {"a": None}),
    ({}, {"n": {"pos_ms": 5}}, {"n": {"pos_ms": 5}}),
])
def test_merge_patch_diff(old, new, patch):
    assert merge_patch_diff(old, new) == patch
    removed = set(old) - set(new)
    if patch is not None and not removed:      # State never removes keys (see ipc.py header)
        assert apply_merge_patch(old, patch) == new


def test_null_means_value_is_null_not_delete():
    state = {"last_touched": {"bank": "A", "ch": 3}, "now": {"pos_ms": 10}}
    out = apply_merge_patch(state, {"last_touched": None, "now": {"pos_ms": None}})
    assert out == {"last_touched": None, "now": {"pos_ms": None}}


def test_roundtrip_on_contract_example():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "examples", "state",
                        "ok-mockup-console.json")
    with open(path) as f:
        a = json.load(f)
    b = json.loads(json.dumps(a))
    b["channels"][2]["level"] = 0.9
    b["now"]["pos_ms"] = 123
    b["last_touched"] = None
    p = merge_patch_diff(a, b)
    assert set(p) == {"channels", "now", "last_touched"} and p["now"] == {"pos_ms": 123}
    assert apply_merge_patch(a, p) == b


# ---- server ----------------------------------------------------------------------

def short_sock():
    d = tempfile.mkdtemp(prefix="cx")
    return os.path.join(d, "chromad.sock")


class Harness:
    def __init__(self, path, patch_hz=30.0, max_line=1 << 20, high_water=256 * 1024):
        self.state = {"n": 0, "blob": ""}
        self.seen = []

        async def handle(req):
            op = req["op"]
            self.seen.append(req)
            if op == "get_state":
                return {"state": self.state}
            if op == "intent":
                return None
            if op == "slow":
                await asyncio.sleep(0.4)
                return {"done": True}
            if op == "fail":
                raise IpcError("nope")
            if op == "crash":
                raise ZeroDivisionError("boom")
            if op == "weird":
                return {"when": object()}
            raise IpcError(f"unknown op {op!r}")
        self.server = IpcServer(path, handle, lambda: dict(self.state), patch_hz=patch_hz,
                                max_line=max_line, high_water=high_water)


class Client:
    def __init__(self, r, w):
        self.r, self.w, self.next = r, w, 1

    @classmethod
    async def open(cls, path):
        r, w = await asyncio.open_unix_connection(path, limit=1 << 22)
        return cls(r, w)

    async def send(self, obj):
        self.w.write(json.dumps(obj).encode() + b"\n")
        await self.w.drain()

    async def req(self, op, **kw):
        rid = self.next
        self.next += 1
        await self.send({"id": rid, "op": op, **kw})
        while True:
            m = await self.read()
            if m.get("id") == rid:
                return m

    async def read(self, timeout=2.0):
        line = await asyncio.wait_for(self.r.readline(), timeout)
        return json.loads(line)

    def close(self):
        self.w.close()


@pytest.fixture
async def srv():
    h = Harness(short_sock())
    await h.server.start()
    yield h
    await h.server.stop()


async def test_socket_is_0600_and_removed_on_stop():
    h = Harness(short_sock())
    await h.server.start()
    mode = stat.S_IMODE(os.stat(h.server.path).st_mode)
    assert mode == 0o600
    await h.server.stop()
    assert not os.path.exists(h.server.path)


async def test_single_instance_and_stale_socket():
    path = short_sock()
    a = Harness(path)
    await a.server.start()
    b = Harness(path)
    with pytest.raises(RuntimeError, match="already running"):
        await b.server.start()
    await a.server.stop()
    # leftover socket file from a crash (nobody listening) is cleaned up
    import socket
    s = socket.socket(socket.AF_UNIX)
    s.bind(path)
    s.close()
    assert os.path.exists(path)
    c = Harness(path)
    await c.server.start()
    cl = await Client.open(path)
    assert (await cl.req("get_state"))["ok"]
    cl.close()
    await c.server.stop()


async def test_socket_path_too_long_is_refused():
    h = Harness("/tmp/" + "x" * 120 + "/chromad.sock")
    with pytest.raises(RuntimeError, match="too long"):
        await h.server.start()


async def test_requests_errors_and_bad_input(srv):
    c = await Client.open(srv.server.path)
    assert await c.req("get_state") == {"id": 1, "ok": True, "state": {"n": 0, "blob": ""}}
    assert await c.req("fail") == {"id": 2, "ok": False, "error": "nope"}
    r = await c.req("crash")
    assert r["ok"] is False and r["error"].startswith("internal error: ZeroDivisionError")
    assert (await c.req("format_disk"))["error"] == "unknown op 'format_disk'"
    r = await c.req("weird")
    assert r["ok"] is False and "unserialisable" in r["error"]
    c.w.write(b"{not json\n")
    assert await c.read() == {"id": None, "ok": False, "error": "invalid JSON"}
    await c.send({"op": "get_state"})
    assert (await c.read())["error"] == "request needs a non-negative integer id"
    await c.send({"id": True, "op": "get_state"})
    assert (await c.read())["id"] is None
    await c.send({"id": 9})
    assert await c.read() == {"id": 9, "ok": False, "error": "request needs an op"}
    c.close()


async def test_line_too_long_closes_connection():
    h = Harness(short_sock(), max_line=1024)
    await h.server.start()
    c = await Client.open(h.server.path)
    c.w.write(b'{"id":1,"op":"get_state","pad":"' + b"x" * 5000 + b'"}\n')
    m = await c.read()
    assert m["ok"] is False and "longer than 1024" in m["error"]
    assert await asyncio.wait_for(c.r.readline(), 2) == b""         # server closed it
    await h.server.stop()


async def test_subscribe_state_then_coalesced_patches(srv):
    c = await Client.open(srv.server.path)
    assert await c.req("subscribe") == {"id": 1, "ok": True}
    assert await c.read() == {"ev": "state", "state": {"n": 0, "blob": ""}}
    t0 = time.monotonic()
    for i in range(1, 301):                       # 300 changes over ~0.5 s
        srv.state["n"] = i
        srv.server.mark_dirty()
        await asyncio.sleep(0.5 / 300)
    await asyncio.sleep(0.1)
    patches = []
    while True:
        try:
            patches.append(await c.read(timeout=0.2))
        except asyncio.TimeoutError:
            break
    elapsed = time.monotonic() - t0
    assert patches and all(p["ev"] == "patch" for p in patches)
    assert patches[-1]["patch"] == {"n": 300}                 # converges to the latest value
    assert len(patches) <= int(elapsed * 30) + 2              # <= 30 Hz
    c.close()


async def test_toasts_and_multiple_clients(srv):
    a, b = await Client.open(srv.server.path), await Client.open(srv.server.path)
    for c in (a, b):
        await c.req("subscribe")
        await c.read()
    srv.server.toast("warn", "Nothing is playing")
    srv.state["n"] = 7
    srv.server.mark_dirty()
    for c in (a, b):
        assert await c.read() == {"ev": "toast", "level": "warn", "text": "Nothing is playing"}
        assert await c.read() == {"ev": "patch", "patch": {"n": 7}}
        c.close()


async def test_intents_keep_order_and_slow_ops_do_not_block(srv):
    c = await Client.open(srv.server.path)
    await c.send({"id": 1, "op": "slow"})
    for i in range(2, 102):
        await c.send({"id": i, "op": "intent", "intent": {"intent": "fader", "ch": 1, "v": i / 200}})
    order = []
    while len(order) < 101:
        m = await c.read()
        order.append(m["id"])
    assert order[:100] == list(range(2, 102)) and order[-1] == 1   # slow op answered last
    seen = [r["intent"]["v"] for r in srv.seen if r["op"] == "intent"]
    assert seen == [i / 200 for i in range(2, 102)]
    c.close()


async def test_slow_consumer_is_resynced_with_full_state():
    import socket
    h = Harness(short_sock(), patch_hz=200, high_water=64 * 1024)
    await h.server.start()
    raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    raw.connect(h.server.path)
    raw.sendall(b'{"id":1,"op":"subscribe"}\n')          # ...then stop reading
    for i in range(80):                                    # ~80 x 50 KB patches
        h.state["blob"] = str(i % 10) * 50_000
        h.state["n"] = i
        h.server.mark_dirty()
        await asyncio.sleep(0.006)
    assert any(cl.stale for cl in h.server.clients)
    raw.setblocking(False)
    buf, got_state = b"", None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and got_state is None:
        try:
            buf += raw.recv(1 << 20)
        except BlockingIOError:
            await asyncio.sleep(0.01)
            continue
        *lines, buf = buf.split(b"\n")
        for line in lines:
            m = json.loads(line)
            if m.get("ev") == "state" and m["state"]["n"] == 79:
                got_state = m
    assert got_state is not None                         # resynced with the latest full state
    raw.close()
    await h.server.stop()
