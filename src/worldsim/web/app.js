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
    ctx.fillStyle = "rgba(0,0,0,0.55)";
    for (const [x, y] of data.roads || []) {
      ctx.fillRect(x * cell - 0.5, y * cell - 0.5, cell + 1, cell + 1);
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

  let best = null, bestDist = Infinity;
  for (const s of gridCache.settlements) {
    const d = Math.max(Math.abs(s.x - tx), Math.abs(s.y - ty));
    if (d <= 2 && d < bestDist) { best = s; bestDist = d; }
  }
  if (best) {
    selectedName = best.name;
    $("p-settlement").value = best.name;
    $("selected-name").textContent =
      `${best.name} — pop ${best.population} @ (${best.x},${best.y})`;
  } else if ($("god-action").value === "nuke"
             || COORD_ACTIONS.has($("god-action").value)) {
    $("p-x").value = tx;
    $("p-y").value = ty;
    $("selected-name").textContent = `aimed at (${tx}, ${ty})`;
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
    li.textContent = `${s.name}: pop ${s.population} · era ${s.era} · ` +
      `army ${s.army} · happy ${s.happiness}${s.frozen ? " · FROZEN" : ""}`;
    list.appendChild(li);
  }
  return true;
}

async function refreshFeed() {
  const feed = await (await api("/api/timeline?limit=25")).json();
  const list = $("feed");
  list.innerHTML = "";
  for (const line of feed.rendered.slice().reverse()) {
    const li = document.createElement("li");
    li.textContent = line.replace(/^\[[^\]]+\]\s*/, "");
    list.appendChild(li);
  }
}

async function refreshWhatsHappening() {
  const events = await (
    await api("/api/timeline?limit=6")
  ).json();
  const latest = events.rendered.slice(-3).reverse();
  const summary = latest.length
    ? latest.map((l) => l.replace(/\[t\d+\]\s*\([^)]*\)\s*/, "")).join(" • ")
    : "the world is quiet…";
  const roads = gridCache ? gridCache.roads.length : 0;
  $("whats-happening").textContent =
    `${summary} — agents have laid ${roads} road tiles so far.`;
}

async function refreshCharts() {
  $("pop-chart").src = "/api/charts/populations.png?" + Date.now();
  $("event-chart").src = "/api/charts/events.png?" + Date.now();
}

async function refreshAll() {
  try {
    const hasWorld = await refreshStatus();
    if (hasWorld) {
      await refreshGrid();
    }
    await refreshFeed();
    await refreshWhatsHappening();
    await refreshCharts();
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
setInterval(refreshWhatsHappening, 3000);
setInterval(refreshCharts, 15000);
refreshAll();
