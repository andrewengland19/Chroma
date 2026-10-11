# =============================================================================
# hub/ipc.py — chromad's Unix-socket NDJSON server (BUILD_ORDER §5.1).
#
# One JSON object per line. Requests carry an integer `id` and an `op`; the
# reply echoes the id: {"id", "ok": true, ...} or {"id", "ok": false, "error"}.
# `subscribe` replies ok, then streams {"ev":"state"} once and {"ev":"patch"}
# (RFC 7386 merge patches) coalesced to <= patch_hz, plus {"ev":"toast"}.
#
# Security (§8.5): the socket is created 0600 (umask during bind + chmod), a
# flock'd lock file makes chromad single-instance, a live socket is never
# stolen, and lines longer than max_line close the connection.
#
# Patch semantics: the State has a fixed shape — keys are never removed — so a
# null in a patch means "the value is now null", not "delete the key".
#
# Ordering: `intent` and `get_state` are handled inline, in arrival order, so
# fader moves apply in sequence; other ops run as tasks so a slow `refresh`
# never stalls the console.
# =============================================================================

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("chroma-hub")

MAX_SOCKET_PATH = 103          # sun_path on macOS is 104 bytes incl. NUL
INLINE_OPS = {"intent", "get_state"}


class IpcError(Exception):
    """An op failed in a way the client should see as {ok:false, error}."""


# ---- RFC 7386 helpers ----------------------------------------------------------

_MISSING = object()


def merge_patch_diff(old: Any, new: Any) -> Optional[dict]:
    """Smallest merge patch turning `old` into `new` (both dicts), or None if equal.
    Lists and scalars are replaced wholesale; removed keys become null."""
    if old == new:
        return None
    patch: dict = {}
    for k, v in new.items():
        o = old.get(k, _MISSING) if isinstance(old, dict) else _MISSING
        if o is _MISSING:
            patch[k] = v
        elif isinstance(v, dict) and isinstance(o, dict):
            sub = merge_patch_diff(o, v)
            if sub is not None:
                patch[k] = sub
        elif o != v:
            patch[k] = v
    if isinstance(old, dict):
        for k in old:
            if k not in new:
                patch[k] = None
    return patch or None


def apply_merge_patch(target: Any, patch: Any) -> Any:
    """Apply a patch the way CHROMA clients do (null = value is null; see header)."""
    if not isinstance(patch, dict):
        return patch
    out = dict(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = apply_merge_patch(out[k], v)
        else:
            out[k] = v
    return out


# ---- server ---------------------------------------------------------------------

class _Client:
    def __init__(self, writer: asyncio.StreamWriter, high_water: int):
        self.writer = writer
        self.high_water = high_water
        self.subscribed = False
        self.stale = False          # fell behind: skip patches, resend full state when drained

    def buffered(self) -> int:
        t = self.writer.transport
        return t.get_write_buffer_size() if t and not t.is_closing() else 0

    def send(self, obj: dict) -> None:
        if self.writer.is_closing():
            return
        self.writer.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode() + b"\n")


class IpcServer:
    def __init__(self, path: str, handle: Callable[[dict], Awaitable[Optional[dict]]],
                 get_state: Callable[[], dict], patch_hz: float = 30.0,
                 max_line: int = 1 << 20, high_water: int = 256 * 1024):
        self.path = path
        self._handle = handle
        self._get_state = get_state
        self._interval = 1.0 / patch_hz
        self._max_line = max_line
        self._high_water = high_water
        self.clients: set = set()
        self._last: dict = {}
        self._dirty = asyncio.Event()
        self._server: Optional[asyncio.AbstractServer] = None
        self._pump_task: Optional[asyncio.Task] = None
        self._lock_fd: Optional[int] = None
        self._tasks: set = set()
        self.patches_sent = 0

    # ---- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        if len(self.path.encode()) > MAX_SOCKET_PATH:
            raise RuntimeError(f"socket path too long for macOS ({len(self.path)} > {MAX_SOCKET_PATH}): "
                               f"{self.path} — use a shorter CHROMA_HOME")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._acquire_lock()
        try:
            await self._clear_stale_socket()
            old = os.umask(0o177)
            try:
                self._server = await asyncio.start_unix_server(self._serve, path=self.path,
                                                               limit=self._max_line)
            finally:
                os.umask(old)
            os.chmod(self.path, 0o600)
        except BaseException:
            self._release_lock()
            raise
        self._last = self._get_state()
        self._pump_task = asyncio.create_task(self._pump())
        log.info(f"IPC listening on {self.path}")

    async def stop(self) -> None:
        if self._pump_task:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except (asyncio.CancelledError, Exception):
                pass
        pending = list(self._tasks)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self._server:
            self._server.close()
        for c in list(self.clients):
            c.writer.close()
        self.clients.clear()
        if self._server:
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=1)
            except (asyncio.TimeoutError, Exception):
                pass
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self._release_lock()

    def _acquire_lock(self) -> None:
        fd = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise RuntimeError(f"another chromad is already running ({self.path}.lock is held)")
        self._lock_fd = fd

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    async def _clear_stale_socket(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            _, w = await asyncio.wait_for(asyncio.open_unix_connection(self.path), timeout=1)
        except (OSError, asyncio.TimeoutError):
            os.unlink(self.path)          # nobody listening: leftover from a crash
            return
        w.close()
        raise RuntimeError(f"{self.path} is in use by a running process")

    # ---- events -------------------------------------------------------------
    def mark_dirty(self) -> None:
        self._dirty.set()

    def toast(self, level: str, text: str) -> None:
        for c in list(self.clients):
            if c.subscribed:
                c.send({"ev": "toast", "level": level, "text": text})

    async def _pump(self) -> None:
        """Coalesce state changes into merge patches, at most once per interval."""
        while True:
            await self._dirty.wait()
            self._dirty.clear()
            try:
                state = self._get_state()
            except Exception:
                log.exception("state assembly failed")
                await asyncio.sleep(self._interval)
                continue
            patch = merge_patch_diff(self._last, state)
            if patch is not None:
                self._last = state
                self.patches_sent += 1
            msg = {"ev": "patch", "patch": patch}
            for c in list(self.clients):
                if not c.subscribed:
                    continue
                if c.stale:                       # recover even when nothing new changed
                    if c.buffered() < self._high_water // 4:
                        c.stale = False
                        c.send({"ev": "state", "state": self._last})
                    continue
                if patch is None:
                    continue
                if c.buffered() > self._high_water:
                    c.stale = True                # stop queueing; resync when it drains
                    continue
                c.send(msg)
            await asyncio.sleep(self._interval)
            if any(c.stale for c in self.clients):
                self._dirty.set()                 # keep checking until stale clients recover

    # ---- connections ----------------------------------------------------------
    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = _Client(writer, self._high_water)
        self.clients.add(client)
        try:
            while True:
                try:
                    line = await reader.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    client.send({"id": None, "ok": False,
                                 "error": f"request line longer than {self._max_line} bytes"})
                    break
                if not line:
                    break
                if line.strip():
                    await self._on_line(client, line)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.clients.discard(client)
            try:
                writer.close()
            except Exception:
                pass

    async def _on_line(self, client: _Client, line: bytes) -> None:
        try:
            req = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            client.send({"id": None, "ok": False, "error": "invalid JSON"})
            return
        rid = req.get("id") if isinstance(req, dict) else None
        if not isinstance(rid, int) or isinstance(rid, bool) or rid < 0:
            client.send({"id": None, "ok": False, "error": "request needs a non-negative integer id"})
            return
        op = req.get("op")
        if not isinstance(op, str) or not op:
            client.send({"id": rid, "ok": False, "error": "request needs an op"})
            return
        if op == "subscribe":
            client.subscribed = True
            client.send({"id": rid, "ok": True})
            client.send({"ev": "state", "state": self._last})
            return
        if op in INLINE_OPS:
            await self._run(client, rid, req)
        else:
            t = asyncio.create_task(self._run(client, rid, req))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def _run(self, client: _Client, rid: int, req: dict) -> None:
        try:
            result = await self._handle(req)
            resp = {"id": rid, "ok": True}
            if result:
                resp.update({k: v for k, v in result.items() if k not in ("id", "ok")})
        except IpcError as e:
            resp = {"id": rid, "ok": False, "error": str(e) or "error"}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception(f"op {req.get('op')!r} failed")
            resp = {"id": rid, "ok": False, "error": f"internal error: {type(e).__name__}: {e}"}
        try:
            client.send(resp)
        except (TypeError, ValueError) as e:       # result not JSON-serialisable
            log.error(f"op {req.get('op')!r} returned an unserialisable result: {e}")
            client.send({"id": rid, "ok": False, "error": f"internal error: unserialisable result ({e})"})
        self.mark_dirty()
