/* WorldSim frontend v1.1 (Sprint 53) — click-to-target + legend. */
"use strict";

const $ = (id) => document.getElementById(id);
const PALETTE = {
  0: "#4a7dbf", // water
  1: "#eedb99", // desert
  2: "#add973", // plains
  3: "#5cb352", // fertile
  4: "#337338", // forest
  5: "#8c8780", // mountain
};
// Actions that need a settlement target vs map coordinates.
const TARGET_ACTIONS = new Set(["smite", "bless", "freeze"]);
const COORD_ACTIONS = new Set([
  "nuke", "spawn_settlement", "trigger_disaster", "terraform_region"]);
// Improvement codes from /api/grid (matches the Improvement enum).
const BUILDING_STYLE = {
  1: { glyph: "f", color: "#e8d44d" }, // farm
  2: { glyph: "w", color: "#b07a3f" }, // sawmill
  3: { glyph: "m", color: "#c9c9c9" }, // mine
  4: { glyph: "g", color: "#e09b3d" }, // granary
};

let gridCache = null;
let selectedName = null;

window.onerror = (message) => {
  showPageError("uncaught", message);
  return false;
};

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const detail = await response.json().catch(
      () => ({ detail: response.statusText }));
    throw new Error(detail.detail || response.statusText);
  }
  return response;
}

async function refreshGrid() {
  const data = await (await api("/api/grid")).json();
  gridCache = data;
  drawMap(data);
}

function showPageError(context, error) {
  // console is invisible inside the packaged pywebview window — put
  // failures where the user can actually see them.
  const message = `[${context}] ${error && error.message ? error.message : error}`;
  $("god-result").textContent = message;
  console.error(message);
}

function drawMap(data) {
  const canvas = $("map");
  const ctx = canvas.getContext("2d");
  try {
    if (!data || !Array.isArray(data.terrain) || !data.size) {
      throw new Error("grid payload incomplete");
    }
    const cell = canvas.width / data.size;
    if (!Number.isFinite(cell) || cell <= 0) {
      throw new Error(`bad cell size (${cell})`);
    }
    // Base coat first so the canvas never sits transparent (reads as
    // black against the dark theme).
    ctx.fillStyle = "#10131a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    for (let y = 0; y < data.size; y++) {
      const row = data.terrain[y];
      if (!Array.isArray(row)) throw new Error(`terrain row ${y} missing`);
      for (let x = 0; x < row.length && x < data.size; x++) {
        ctx.fillStyle = PALETTE[row[x]] || "#333";
        ctx.fillRect(x * cell, y * cell, cell + 0.5, cell + 0.5);
      }
    }
    ctx.fillStyle = "#2e2e33";
    for (const [x, y] of data.roads || []) {
      ctx.fillRect(x * cell + cell * 0.15, y * cell + cell * 0.15,
                   cell * 0.7, cell * 0.7);
    }
    // Highways (inter-city projects) render in road-brown on top.
    ctx.fillStyle = "#8a5a2a";
    for (const [x, y] of data.highways || []) {
      ctx.fillRect(x * cell + cell * 0.1, y * cell + cell * 0.1,
                   cell * 0.8, cell * 0.8);
    }
    ctx.font = `${Math.max(7, cell * 1.6)}px monospace`;
    ctx.textAlign = "center";
    for (const [x, y, kind] of data.buildings || []) {
      const style = BUILDING_STYLE[kind];
      if (!style) continue;
      ctx.fillStyle = style.color;
      ctx.fillText(style.glyph,
                   (x + 0.5) * cell, (y + 0.85) * cell);
    }
    ctx.fillStyle = "#9aa0a6";
    ctx.font = `${Math.max(7, cell * 2.2)}px monospace`;
    for (const r of data.ruins || []) {
      ctx.fillText("X", r.x * cell, (r.y + 0.85) * cell);
    }
    for (const z of data.zones || []) {
      ctx.fillStyle = "rgba(255,140,0,0.25)";
      const d = z.radius * 2 + 1;
      ctx.fillRect((z.cx - z.radius) * cell, (z.cy - z.radius) * cell,
                   d * cell, d * cell);
    }
    for (const s of data.settlements || []) {
      const radius = Math.max(4, Math.min(14, s.population * 0.09));
      ctx.beginPath();
      ctx.arc(s.x * cell, s.y * cell, radius, 0, Math.PI * 2);
      ctx.fillStyle = s.name === selectedName ? "gold" : "crimson";
      ctx.fill();
      ctx.strokeStyle = "white";
      ctx.lineWidth = s.name === selectedName ? 1.6 : 0.8;
      ctx.stroke();
      ctx.fillStyle = "white";
      ctx.textAlign = "center";
      ctx.fillText(s.name.slice(0, 8), s.x * cell, (s.y - 1.2) * cell);
    }
    // War overlay: red dashed lines between warring civilizations.
    if ((data.wars || []).length) {
      ctx.save();
      ctx.strokeStyle = "#ff3b3b";
      ctx.lineWidth = Math.max(1.2, cell * 0.22);
      ctx.setLineDash([cell * 0.9, cell * 0.7]);
      for (const w of data.wars) {
        ctx.beginPath();
        ctx.moveTo((w.a.x + 0.5) * cell, (w.a.y + 0.5) * cell);
        ctx.lineTo((w.b.x + 0.5) * cell, (w.b.y + 0.5) * cell);
        ctx.stroke();
      }
      // Crossed swords at each war's midpoint.
      ctx.setLineDash([]);
      ctx.font = `${Math.max(9, cell * 2)}px monospace`;
      ctx.fillStyle = "#ff3b3b";
      for (const w of data.wars) {
        const mx = (w.a.x + w.b.x) / 2 + 0.5;
        const my = (w.a.y + w.b.y) / 2 + 0.5;
        ctx.fillText("⚔", mx * cell, my * cell);
      }
      ctx.restore();
    }
  } catch (e) {
    showPageError("map render", e);
  }
}

// Click-to-target: select a nearby settlement, or aim coordinate actions.
$("map").onclick = (event) => {
  if (!gridCache) return;
  const canvas = $("map");
  const cell = canvas.width / gridCache.size;
  const rect = canvas.getBoundingClientRect();
  const scale = canvas.width / rect.width;
  const tx = Math.floor((event.clientX - rect.left) * scale / cell);
  const ty = Math.floor((event.clientY - rect.top) * scale / cell);

  const action = $("god-action").value;
  let best = null, bestDist = Infinity;
  for (const s of gridCache.settlements) {
    const d = Math.max(Math.abs(s.x - tx), Math.abs(s.y - ty));
    if (d <= 2 && d < bestDist) { best = s; bestDist = d; }
  }
  // Coordinate actions always take the clicked tile as aim — even on
  // top of a settlement (nuke THIS city must not keep stale coords).
  if (COORD_ACTIONS.has(action)) {
    $("p-x").value = tx;
    $("p-y").value = ty;
    $("selected-name").textContent =
      `${action} aimed at (${tx}, ${ty})` +
      (best ? ` — ${best.name} is here` : "");
  } else if (best) {
    selectedName = best.name;
    $("p-settlement").value = best.name;
    $("selected-name").textContent =
      `${best.name} — pop ${best.population} @ (${best.x},${best.y})`;
  } else {
    selectedName = null;
    $("selected-name").textContent =
      `(${tx}, ${ty}) — no settlement there`;
  }
  drawMap(gridCache);
};

async function refreshStatus() {
  let status;
  try {
    status = await (await api("/api/status")).json();
  } catch (e) {
    // No world loaded (fresh desktop launch): offer creation.
    $("create-box").style.display = "block";
    $("clock").textContent = "— no world loaded";
    return false;
  }
  $("create-box").style.display = "none";
  $("clock").textContent =
    `— tick ${status.tick} (${status.date}) | seed ${status.seed}`;
  const list = $("settlements");
  list.innerHTML = "";
  for (const s of status.settlements) {
    const li = document.createElement("li");
    li.textContent =
      `${s.name}: pop ${s.population} · era ${s.era} · ` +
      `army ${s.army} · happy ${s.happiness}` +
      `${s.frozen ? " · FROZEN" : ""}\n` +
      `food ${s.food_stock ?? "?"} · wood ${s.wood ?? "?"} · ` +
      `stone ${s.stone ?? "?"} · metal ${s.metal ?? "?"}`;
    li.style.whiteSpace = "pre-line";
    list.appendChild(li);
  }
  return true;
}

const CATEGORY_COLORS = {
  warfare: "#ff6b6b",
  diplomacy: "#7fb3ff",
  trade: "#7fd98a",
  divine: "#ffd166",
  counsel: "#ffa8d8",
  disasters: "#c792ea",
  civilization: "#64d8cb",
  other: "#9aa0a6",
};

async function refreshFeed() {
  const data = await (await api("/api/timeline?limit=40")).json();
  // Summary header: world pulse in one line.
  const roads = gridCache ? gridCache.roads.length : 0;
  const highways = gridCache ? (gridCache.highways || []).length : 0;
  const wars = gridCache ? (gridCache.wars || []).length : 0;
  const pops = gridCache
    ? gridCache.settlements.map((s) => s.population)
    : [];
  const totalPop = pops.reduce((a, b) => a + b, 0);
  $("timeline-summary").textContent =
    `tick ${gridCache ? gridCache.tick : "?"} · ` +
    `${totalPop} people in ${pops.length} cities · ` +
    `${roads} road tiles · ${highways} highway tiles · ` +
    `${wars} active war${wars === 1 ? "" : "s"}`;
  const list = $("feed");
  list.innerHTML = "";
  for (const line of data.rendered.slice().reverse()) {
    const match = line.match(/^\[t(\d+)\] \((\w+)\)\s*(.*)$/);
    const li = document.createElement("li");
    if (match) {
      const [, tick, category, rest] = match;
      li.innerHTML =
        `<span class="tstamp">t${tick}</span>` +
        `<span class="cat" style="color:${CATEGORY_COLORS[category] ||
          CATEGORY_COLORS.other}">${category}</span>` +
        `<span class="desc"></span>`;
      li.querySelector(".desc").textContent = rest;
    } else {
      li.textContent = line;
    }
    list.appendChild(li);
  }
}

async function refreshCharts() {
  $("pop-chart").src = "/api/charts/populations.png?" + Date.now();
  $("event-chart").src = "/api/charts/events.png?" + Date.now();
}

// Click a chart to open it full-size.
for (const id of ["pop-chart", "event-chart"]) {
  $(id).onclick = () => window.open($(id).src, "_blank");
}

async function refreshAll() {
  try {
    const hasWorld = await refreshStatus();
    if (hasWorld) {
      await refreshGrid();
      await refreshFeed();
      await refreshCharts();
    }
  } catch (e) {
    showPageError("refresh", e);
  }
}

$("btn-step").onclick = () => api("/api/step", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ ticks: 1 }),
}).then(refreshAll);

$("btn-step10").onclick = () => api("/api/step", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ ticks: 10 }),
}).then(refreshAll);

$("btn-run").onclick = () => api("/api/run", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ interval_ticks: 500 }),
});

$("btn-pause").onclick = () => api("/api/pause", { method: "POST" });

$("btn-undo").onclick = () =>
  api("/api/undo", { method: "POST" })
    .then((r) => r.json())
    .then((r) => { $("god-result").textContent = `undid: ${r.undid}`; })
    .then(refreshAll)
    .catch((e) => { $("god-result").textContent = e.message; });

$("god-form").onsubmit = async (event) => {
  event.preventDefault();
  const action = $("god-action").value;
  const body = { action, params: {}, confirm: $("p-confirm").checked };
  if (TARGET_ACTIONS.has(action)) {
    const target = $("p-settlement").value.trim();
    if (!target) {
      $("god-result").textContent =
        "no target — click a red settlement on the map first";
      return;
    }
    body.params.settlement = target;
  } else if (COORD_ACTIONS.has(action)) {
    body.params.x = Number($("p-x").value);
    body.params.y = Number($("p-y").value);
  }
  if (action === "smite") body.params.amount = Number($("p-amount").value);
  if (action === "bless") {
    body.params.resource = $("p-resource").value;
    body.params.amount = Number($("p-amount").value);
  }
  if (action === "trigger_disaster") body.params.disaster_type = "plague";
  if (action === "terraform_region") {
    body.params.terrain = $("p-terrain").value;
    body.params.radius = 4;
  }
  try {
    const result = await (await api(`/api/god/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })).json();
    $("god-result").textContent = JSON.stringify(result.after ?? result);
  } catch (e) {
    $("god-result").textContent = `refused: ${e.message}`;
  }
  refreshAll();
};

$("btn-new").onclick = () =>
  api("/api/new", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      seed: Number($("new-seed").value),
      settlements: Number($("new-settlements").value),
    }),
  }).then(refreshAll)
    .catch((e) => { $("god-result").textContent = e.message; });

function connectSocket() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${location.host}/ws/status`);
  ws.onmessage = (event) => { if (event.data) refreshStatus(); };
  ws.onclose = () => setTimeout(connectSocket, 2000);
}
connectSocket();

setInterval(refreshStatus, 3000);
setInterval(refreshFeed, 3000);
// Keep the map live while the world runs (the WS ticker only updates
// status — without this the canvas freezes at its last explicit step).
setInterval(() => {
  refreshGrid().catch((e) => showPageError("map refresh", e));
}, 3000);
setInterval(refreshCharts, 15000);
refreshAll();
