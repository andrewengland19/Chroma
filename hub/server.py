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
from fastapi.responses import HTMLResponse, JSONResponse, Response

from config import Config
from engine import Engine, ARTWORK_FILE

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
# WebSocket — snapshot on connect, then live events
# =============================================================================

@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    await sock.send_json({"event": "state", **engine.snapshot()})
    q = engine.bus.subscribe()
    try:
        while True:
            await sock.send_json(await q.get())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        engine.bus.unsubscribe(q)


# =============================================================================
# Tiny built-in status page (sanity check from a phone browser; real UI = Pass 4)
# =============================================================================

_PAGE = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>CHROMA hub</title>
<style>
  :root{color-scheme:dark}
  body{margin:0;font:16px -apple-system,system-ui,sans-serif;background:#0e0e0e;color:#eee;
       display:flex;flex-direction:column;align-items:center;gap:16px;padding:24px}
  #art{width:min(70vw,320px);aspect-ratio:1;border-radius:16px;background:#1a1a1a #222;
       object-fit:cover;box-shadow:0 8px 40px #0008}
  #track{font-size:18px;font-weight:600;text-align:center;min-height:1.4em}
  #sub{color:#999;text-align:center;margin-top:-8px}
  #pal{display:flex;gap:8px}
  .sw{width:44px;height:44px;border-radius:10px;box-shadow:inset 0 0 0 1px #fff2}
  #st{color:#777;font-size:13px}
</style>
<img id=art alt="album art">
<div id=track>—</div>
<div id=sub></div>
<div id=pal></div>
<div id=st>connecting…</div>
<script>
const hsl=c=>`hsl(${c.h/65535*360} ${c.s/65535*100}% ${Math.max(20,c.b/65535*60)}%)`;
function render(s){
  document.getElementById('track').textContent=s.track||'Nothing playing';
  document.getElementById('sub').textContent=(s.device?('📺 '+s.device):'')+(s.album?('  ·  '+s.album):'');
  const pal=document.getElementById('pal');pal.innerHTML='';
  (s.colors||[]).forEach(c=>{const d=document.createElement('div');d.className='sw';d.style.background=hsl(c);pal.appendChild(d);});
  document.getElementById('st').textContent=(s.connected?'● connected':'○ disconnected')+' · '+(s.lights||0)+' lights'+(s.enabled?'':' · paused');
  if(s.track)document.getElementById('art').src='/artwork.jpg?'+Date.now();
}
fetch('/state').then(r=>r.json()).then(render);
const proto=location.protocol==='https:'?'wss':'ws';
const w=new WebSocket(proto+'://'+location.host+'/ws');
w.onmessage=e=>{const m=JSON.parse(e.data);if(m.event==='state')render(m);else fetch('/state').then(r=>r.json()).then(render);};
w.onclose=()=>document.getElementById('st').textContent='○ socket closed';
</script>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return _PAGE


if __name__ == "__main__":
    cfg = Config.load()
    uvicorn.run(app, host=cfg.api_host, port=cfg.api_port, log_level="info")
