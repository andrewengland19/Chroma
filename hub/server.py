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


@app.post("/scene/clear")
async def post_scene_clear():
    return await engine.scene_clear()


@app.post("/refresh")
async def post_refresh():
    result = await engine.refresh()
    engine.bus.publish("state", **engine.snapshot())
    result.pop("state", None)
    return result


@app.post("/backend")
async def post_backend(body: dict):
    return await engine.set_backend((body or {}).get("backend", ""))


@app.get("/keystatus")
async def get_keystatus():
    from keystore import has_anthropic_key
    return {"claude_available": has_anthropic_key()}


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


# ── Pass 5: natural-language light agent ─────────────────────────────────────

@app.post("/agent")
async def agent(body: dict):
    import lights_agent
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"error": "empty prompt"}, status_code=400)
    if engine.enabled:  # stop album-art painting from overwriting the agent's look
        await engine.set_enabled(False)
    try:
        return await asyncio.get_running_loop().run_in_executor(None, lights_agent.run, prompt)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/agent/undo")
async def agent_undo():
    import lights_agent
    return await asyncio.get_running_loop().run_in_executor(None, lights_agent.undo)


@app.post("/agent/save")
async def agent_save(body: dict):
    import lights_agent
    return await asyncio.get_running_loop().run_in_executor(
        None, lights_agent.t_save_scene, {"name": body.get("name", "")})


# ── Pass 5: Lights tab (hub/web/lights.html) ─────────────────────────────────

async def _ex(fn, *a):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *a)


@app.get("/lights")
async def lights_get():
    import lights_agent
    return await _ex(lights_agent.room_view)


@app.post("/lights/set")
async def lights_set(body: dict):
    import lights_agent
    if engine.enabled:  # manual control wins over album-art painting
        await engine.set_enabled(False)
    return await _ex(lights_agent.t_set_lights, {"changes": body.get("changes", [])})


@app.get("/scenes")
async def scenes_get():
    import lights_agent
    return await _ex(lights_agent.scenes_view)


@app.post("/scenes/recall")
async def scenes_recall(body: dict):
    import lights_agent
    if engine.enabled:
        await engine.set_enabled(False)
    return await _ex(lights_agent.t_recall_scene, {"name": body.get("name", "")})


# ── Pass 5: TV remote (hub/web/remote.html) — wraps the `tv` CLI ──────────────

TV_BIN = "/usr/local/bin/tv"
TV_SIMPLE = {"play", "pause", "stop", "toggle", "next", "prev", "up", "down", "left",
             "right", "select", "ok", "menu", "home", "top", "wake", "sleep", "now", "apps"}


async def _tv(*argv: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        TV_BIN, *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "output": "tv: timed out"}
    return {"ok": proc.returncode == 0, "output": out.decode(errors="replace").strip()}


@app.post("/tv")
async def tv(body: dict):
    cmd = str(body.get("cmd", "")).lower()
    arg = str(body.get("arg", "")).strip()
    if cmd in TV_SIMPLE:
        return await _tv(cmd)
    if cmd in ("skip", "back") and (not arg or arg.isdigit()):
        return await _tv(cmd, *([arg] if arg else []))
    if cmd == "vol" and arg.split()[:1] in (["up"], ["down"]):
        return await _tv("vol", *arg.split()[:2])
    if cmd == "launch" and arg:
        return await _tv("launch", arg)
    return JSONResponse({"ok": False, "output": f"unsupported tv command {cmd!r}"}, status_code=400)


@app.get("/tv/apps")
async def tv_apps():
    import re
    r = await _tv("apps")
    names = [m.group(1) for m in re.finditer(r"^\s*\d+\.\s+(.+?)\s+\(", r["output"], re.M)]
    return sorted(set(names))


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
    elif c == "scene_clear":
        await engine.scene_clear()
    elif c == "set_backend":
        await engine.set_backend(cmd.get("backend", ""))
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
