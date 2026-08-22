"use strict";
// CHROMA hub — web GUI. One WebSocket carries state + events out and commands in.

let ws, state = {}, perLight = {}, positions = {}, dragging = null, lastArtTrack = null;

// ---------- WebSocket ----------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => { setStatus(false, "reconnecting…"); setTimeout(connect, 1500); };
  ws.onerror = () => ws.close();
}
function send(cmd) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(cmd)); }

function handle(m) {
  switch (m.event) {
    case "state": applyState(m); break;
    case "colors_pushed":
      if (m.per_light) { perLight = {}; m.per_light.forEach(p => perLight[p.label] = p); drawRoom(); }
      break;
    case "ai_reasoning": renderAI(m); break;
    case "track_change": refreshArt(m.track); break;
    case "status": setStatus(state.connected, m.message || ""); break;
  }
}

// ---------- apply full state ----------
function applyState(s) {
  state = s;
  perLight = {}; (s.per_light || []).forEach(p => perLight[p.label] = p);
  if (!dragging) {
    positions = {};
    const L = (s.layout && s.layout.lights) || {};
    for (const k in L) positions[k] = { x: L[k].x, y: L[k].y };
  }
  // header
  document.getElementById("track").textContent = s.track || "Nothing playing";
  document.getElementById("sub").textContent =
    (s.device ? "📺 " + s.device : "") + (s.album ? "  ·  " + s.album : "");
  setStatus(s.connected, (s.lights || 0) + " lights" + (s.enabled ? "" : " · paused"));
  document.getElementById("power").style.opacity = s.enabled ? 1 : 0.45;
  refreshArt(s.track);
  // modes
  document.querySelectorAll(".mode").forEach(b =>
    b.classList.toggle("active", b.dataset.mode === s.mode));
  showPanel(s.mode);
  renderScene(s.scene);
  renderAIStatus(s.ai);
  // sliders (don't fight an active drag)
  const c = s.config || {};
  ["brightness", "brightness_dynamic_range", "brightness_floor", "transition_ms"].forEach(id => {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el && c[id] != null) el.value = c[id];
  });
  // paint + ai
  renderSwatches(c.paint_colors || []);
  document.querySelectorAll("#distrib button").forEach(b =>
    b.classList.toggle("active", b.dataset.dist === c.paint_distribution));
  if (s.ai) renderAI(s.ai);
  drawRoom();
}

function setStatus(on, text) {
  const dot = document.getElementById("dot");
  dot.className = "dot " + (on ? "on" : "off");
  document.getElementById("statustext").textContent = text;
}

function showPanel(mode) {
  document.getElementById("paintPanel").hidden = mode !== "paint";
  document.getElementById("aiPanel").hidden = mode !== "ai";
}

// ---------- artwork ----------
function refreshArt(track) {
  if (track === lastArtTrack) return;
  lastArtTrack = track;
  const src = "/artwork.jpg?t=" + Date.now();
  document.getElementById("art").src = src;
  const pa = document.getElementById("paintArt");
  pa.src = src;
  pa.onload = buildSampleCanvas;
}

// ---------- room canvas (drag lights) ----------
const room = document.getElementById("room");
const rctx = room.getContext("2d");
const PAD = 34, R = 17;

function toCanvas(nx, ny) {
  return [PAD + nx * (room.width - 2 * PAD), PAD + ny * (room.height - 2 * PAD)];
}
function fromCanvas(cx, cy) {
  return [
    Math.max(0, Math.min(1, (cx - PAD) / (room.width - 2 * PAD))),
    Math.max(0, Math.min(1, (cy - PAD) / (room.height - 2 * PAD))),
  ];
}
function evtCanvas(e) {
  const r = room.getBoundingClientRect();
  return [(e.clientX - r.left) / r.width * room.width, (e.clientY - r.top) / r.height * room.height];
}
function drawRoom() {
  rctx.clearRect(0, 0, room.width, room.height);
  rctx.strokeStyle = "#2f2f3a"; rctx.lineWidth = 2;
  rctx.strokeRect(PAD / 2, PAD / 2, room.width - PAD, room.height - PAD);
  for (const label in positions) {
    const p = positions[label];
    const [cx, cy] = toCanvas(p.x, p.y);
    const hex = (perLight[label] && perLight[label].hex) || "#444";
    rctx.beginPath(); rctx.arc(cx, cy, R, 0, 7);
    rctx.fillStyle = hex; rctx.fill();
    rctx.lineWidth = 2; rctx.strokeStyle = "#ffffff88"; rctx.stroke();
    rctx.fillStyle = "#aaa"; rctx.font = "11px system-ui"; rctx.textAlign = "center";
    rctx.fillText(label, cx, cy + R + 13);
  }
}
function hitLight(cx, cy) {
  for (const label in positions) {
    const [x, y] = toCanvas(positions[label].x, positions[label].y);
    if ((cx - x) ** 2 + (cy - y) ** 2 <= (R + 5) ** 2) return label;
  }
  return null;
}
room.addEventListener("pointerdown", (e) => {
  const [cx, cy] = evtCanvas(e); const hit = hitLight(cx, cy);
  if (hit) { dragging = hit; room.setPointerCapture(e.pointerId); }
});
room.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  const [cx, cy] = evtCanvas(e); const [nx, ny] = fromCanvas(cx, cy);
  positions[dragging] = { x: nx, y: ny }; drawRoom();
});
room.addEventListener("pointerup", (e) => {
  if (!dragging) return;
  const p = positions[dragging];
  send({ cmd: "move_light", label: dragging, x: p.x, y: p.y });
  dragging = null;
});

// ---------- paint: sample + magnifier ----------
let sampleCanvas = null, sctx = null, natW = 0, natH = 0;
function buildSampleCanvas() {
  const img = document.getElementById("paintArt");
  if (!img.naturalWidth) return;
  natW = img.naturalWidth; natH = img.naturalHeight;
  sampleCanvas = document.createElement("canvas");
  sampleCanvas.width = natW; sampleCanvas.height = natH;
  sctx = sampleCanvas.getContext("2d");
  sctx.drawImage(img, 0, 0);
}
function sampleHex(fx, fy) {
  if (!sctx) return null;
  const d = sctx.getImageData(Math.floor(fx * natW), Math.floor(fy * natH), 1, 1).data;
  return "#" + [d[0], d[1], d[2]].map(v => v.toString(16).padStart(2, "0")).join("");
}

const pArt = document.getElementById("paintArt");
const lens = document.getElementById("lens"), lctx = lens.getContext("2d");
let holdTimer = null, lensOn = false, curF = [0, 0];

function artFrac(e) {
  const r = pArt.getBoundingClientRect();
  return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
          Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))];
}
function drawLens(e) {
  if (!sctx) return;
  const [fx, fy] = curF, span = 34, z = lens.width / span;
  lctx.imageSmoothingEnabled = false;
  lctx.clearRect(0, 0, lens.width, lens.height);
  lctx.drawImage(sampleCanvas, fx * natW - span / 2, fy * natH - span / 2, span, span,
                 0, 0, lens.width, lens.height);
  lctx.strokeStyle = "#fff"; lctx.lineWidth = 1;
  lctx.beginPath(); lctx.moveTo(lens.width / 2, 0); lctx.lineTo(lens.width / 2, lens.height);
  lctx.moveTo(0, lens.height / 2); lctx.lineTo(lens.width, lens.height / 2); lctx.stroke();
  const r = pArt.getBoundingClientRect();
  lens.style.left = (fx * r.width - lens.width - 10) + "px";
  lens.style.top = (fy * r.height - lens.height - 10) + "px";
}
pArt.addEventListener("pointerdown", (e) => {
  curF = artFrac(e); pArt.setPointerCapture(e.pointerId);
  holdTimer = setTimeout(() => { lensOn = true; lens.hidden = false; drawLens(e); }, 220);
});
pArt.addEventListener("pointermove", (e) => { curF = artFrac(e); if (lensOn) drawLens(e); });
pArt.addEventListener("pointerup", (e) => {
  clearTimeout(holdTimer);
  curF = artFrac(e);
  const hex = sampleHex(curF[0], curF[1]);
  lensOn = false; lens.hidden = true;
  if (hex) addFocus(hex, curF[0], curF[1]);
});

function currentFocus() { return (state.config && state.config.paint_colors) || []; }
function currentDist() { return (state.config && state.config.paint_distribution) || "round_robin"; }
function addFocus(hex, x, y) {
  const colors = currentFocus().concat([{ hex, x, y }]);
  send({ cmd: "paint_set", distribution: currentDist(), colors });
}
function renderSwatches(colors) {
  const box = document.getElementById("swatches"); box.innerHTML = "";
  colors.forEach((c, i) => {
    const d = document.createElement("div"); d.className = "sw"; d.style.background = c.hex;
    const x = document.createElement("span"); x.className = "x"; x.textContent = "×";
    x.onclick = () => {
      const next = currentFocus().slice(); next.splice(i, 1);
      if (next.length) send({ cmd: "paint_set", distribution: currentDist(), colors: next });
      else send({ cmd: "paint_clear" });
    };
    d.appendChild(x); box.appendChild(d);
  });
}
document.querySelectorAll("#distrib button").forEach(b =>
  b.onclick = () => send({ cmd: "paint_set", distribution: b.dataset.dist, colors: currentFocus() }));
document.getElementById("clearPaint").onclick = () => send({ cmd: "paint_clear" });

// ---------- AI panel ----------
function renderAI(ai) {
  document.getElementById("aiModel").textContent = ai.model || "";
  document.getElementById("aiMood").textContent = ai.mood || "";
  const pal = document.getElementById("aiPalette"); pal.innerHTML = "";
  (ai.palette || []).forEach(hex => {
    const d = document.createElement("div"); d.className = "sw"; d.style.background = hex; pal.appendChild(d);
  });
  if (ai.reasoning) document.getElementById("aiReasoning").textContent = ai.reasoning;
  document.getElementById("aiError").textContent =
    (ai.ok === false && ai.error) ? "fell back to deterministic — " + ai.error : "";
}

// ---------- controls ----------
document.querySelectorAll(".mode").forEach(b =>
  b.onclick = () => send({ cmd: "set_mode", mode: b.dataset.mode }));
document.querySelectorAll(".transport [data-action]").forEach(b =>
  b.onclick = () => send({ cmd: "transport", action: b.dataset.action }));
document.getElementById("power").onclick = () =>
  send({ cmd: "set_enabled", on: !state.enabled });

let sliderTimer = null;
["brightness", "brightness_dynamic_range", "brightness_floor", "transition_ms"].forEach(id => {
  const el = document.getElementById(id);
  el.addEventListener("input", () => {
    clearTimeout(sliderTimer);
    sliderTimer = setTimeout(() => {
      const v = id === "transition_ms" ? parseInt(el.value) : parseFloat(el.value);
      send({ cmd: "set_config", patch: { [id]: v } });
    }, 120);
  });
});

function renderScene(sc) {
  const bar = document.getElementById("sceneBar");
  if (!sc || !sc.saved) { bar.hidden = true; return; }
  bar.hidden = false;
  const kind = sc.type === "ai" ? "AI" : "paint";
  document.getElementById("sceneLabel").textContent =
    (sc.active ? "★ playing this album's saved " : "★ saved ") + kind + " scene for this album";
}
document.getElementById("forgetScene").onclick = () => send({ cmd: "scene_clear" });

function renderAIStatus(ai) {
  const bar = document.getElementById("aiBanner");
  if (!ai) { bar.hidden = true; return; }
  // Show the offline banner when the local model is unreachable and we're not
  // already on Claude. "Use Claude" is enabled only if a key is configured.
  const showBanner = ai.ollama_reachable === false && ai.backend !== "claude";
  bar.hidden = !showBanner;
  if (showBanner) {
    const btn = document.getElementById("useClaude");
    btn.disabled = !ai.claude_available;
    btn.title = ai.claude_available ? "" : "Run: lite show setkey";
    document.getElementById("aiBannerText").textContent =
      "Local AI (Ollama) offline" + (ai.claude_available ? "" : " · no Claude key");
  }
  const chip = document.getElementById("aiModel");
  if (chip) chip.textContent = (ai.model || "") + (ai.backend === "claude" ? " · Claude" : " · local");
}
document.getElementById("useClaude").onclick = () => send({ cmd: "set_backend", backend: "claude" });

connect();
