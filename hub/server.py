#!/usr/bin/env python3
# =============================================================================
# hub/server.py  —  Pass 3: local control plane (FastAPI + WebSocket).
#
# Embeds the Pass 2 Engine in the SAME asyncio loop as FastAPI, so the API calls
# engine methods directly and the engine pushes events straight to WebSocket
# clients (in-memory EventBus) — no polling, no command files. LAN-bound, no auth
# (trusted home network).
#
# Run:  ./.venv/bin/python server.py        (or `lite show start`)
# =============================================================================

import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from config import Config
from engine import Engine, ARTWORK_FILE

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

engine: Engine | None = None
_engine_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, _engine_task
    loop = asyncio.get_running_loop()
    engine = Engine(loop, install_signals=False)  # uvicorn owns signals
    _engine_task = loop.create_task(engine.run())
    try:
        yield
    finally:
        engine.stop()
        try:
            await asyncio.wait_for(_engine_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _engine_task.cancel()


app = FastAPI(title="CHROMA hub", lifespan=lifespan)


# =============================================================================
# REST
# =============================================================================

@app.get("/state")
async def get_state():
    return engine.snapshot()


@app.get("/config")
async def get_config():
    return engine.snapshot()["config"]


@app.post("/config")
async def post_config(partial: dict):
    if not isinstance(partial, dict):
        return JSONResponse({"error": "expected a JSON object"}, status_code=400)
    return await engine.set_config(partial)


@app.post("/transport")
async def post_transport(body: dict):
    action = (body or {}).get("action", "")
    ok = await engine.transport(action)
    if not ok:
        return JSONResponse(
            {"error": f"invalid action {action!r} or Apple TV not connected"},
            status_code=400,
        )
    return {"ok": True, "action": action}


@app.post("/enabled")
async def post_enabled(body: dict):
    on = bool((body or {}).get("on", True))
    return await engine.set_enabled(on)


@app.post("/mode")
async def post_mode(body: dict):
    return await engine.set_mode((body or {}).get("mode", ""))


@app.post("/paint")
async def post_paint(body: dict):
    body = body or {}
    return await engine.set_focus(body.get("distribution"), body.get("colors") or [])


@app.post("/paint/clear")
async def post_paint_clear():
    return await engine.paint_clear()


@app.get("/layout")
async def get_layout():
    return engine.layout.to_dict()


@app.post("/layout")
async def post_layout(partial: dict):
    if not isinstance(partial, dict):
        return JSONResponse({"error": "expected a JSON object"}, status_code=400)
    return await engine.set_layout(partial)


@app.get("/enhancer")
async def get_enhancer():
    return engine.snapshot()["ai"]


@app.post("/stop")
async def post_stop():
    engine.stop()
    return {"ok": True, "stopping": True}


@app.get("/artwork.jpg")
async def get_artwork():
    if not os.path.exists(ARTWORK_FILE):
        return Response(status_code=404)
    with open(ARTWORK_FILE, "rb") as f:
        return Response(content=f.read(), media_type="image/jpeg")


# =============================================================================
# WebSocket — bidirectional: streams events out, accepts command frames in
# =============================================================================

async def _dispatch(cmd: dict) -> None:
    c = cmd.get("cmd")
    if c == "set_mode":
        await engine.set_mode(cmd.get("mode", ""))
    elif c == "set_config":
        await engine.set_config(cmd.get("patch") or {})
    elif c == "set_enabled":
        await engine.set_enabled(bool(cmd.get("on", True)))
    elif c == "transport":
        await engine.transport(cmd.get("action", ""))
    elif c == "move_light":
        await engine.move_light(cmd.get("label", ""),
                                float(cmd.get("x", 0.5)), float(cmd.get("y", 0.5)))
    elif c == "set_layout":
        await engine.set_layout({k: cmd[k] for k in ("lights", "region_w", "region_h") if k in cmd})
    elif c == "paint_set":
        await engine.set_focus(cmd.get("distribution"), cmd.get("colors") or [])
    elif c == "paint_clear":
        await engine.paint_clear()
    # After any mutating command, push fresh state to every client.
    if c and c != "resync":
        engine.bus.publish("state", **engine.snapshot())
    elif c == "resync":
        engine.bus.publish("state", **engine.snapshot())


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    await sock.send_json({"event": "state", **engine.snapshot()})
    q = engine.bus.subscribe()

    async def pump():          # engine events → client
        while True:
            await sock.send_json(await q.get())

    async def recv():          # client commands → engine
        while True:
            await _dispatch(await sock.receive_json())

    try:
        await asyncio.gather(pump(), recv())
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        engine.bus.unsubscribe(q)


# =============================================================================
# Web GUI — served from hub/web/ (index.html at /). Mounted LAST so the API
# routes above take precedence.
# =============================================================================

app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    cfg = Config.load()
    uvicorn.run(app, host=cfg.api_host, port=cfg.api_port, log_level="info")
