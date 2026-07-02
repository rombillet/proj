const canvas = document.getElementById("mapCanvas");
const ctx = canvas.getContext("2d");
const statusEl = document.getElementById("status");
const nodeCountEl = document.getElementById("nodeCount");
const roadCountEl = document.getElementById("roadCount");
const lightCountEl = document.getElementById("lightCount");
const matchedCountEl = document.getElementById("matchedCount");
const signalNodeCountEl = document.getElementById("signalNodeCount");
const outsideCountEl = document.getElementById("outsideCount");
const showNodesEl = document.getElementById("showNodes");
const showUnmatchedEl = document.getElementById("showUnmatched");

const state = {
  roads: [],
  nodes: [],
  lights: [],
  roadBounds: null,
  allBounds: null,
  view: { scale: 1, x: 0, y: 0 },
  dragging: false,
  lastPointer: null,
};

const metersPerLon = 111320 * Math.cos((31.25 * Math.PI) / 180);
const metersPerLat = 110540;

function project(lon, lat) {
  return [lon * metersPerLon, -lat * metersPerLat];
}

function extendBounds(bounds, point) {
  if (!Number.isFinite(point[0]) || !Number.isFinite(point[1])) return bounds;
  if (!bounds) {
    return { minX: point[0], minY: point[1], maxX: point[0], maxY: point[1] };
  }
  bounds.minX = Math.min(bounds.minX, point[0]);
  bounds.minY = Math.min(bounds.minY, point[1]);
  bounds.maxX = Math.max(bounds.maxX, point[0]);
  bounds.maxY = Math.max(bounds.maxY, point[1]);
  return bounds;
}

function fitBounds(bounds) {
  if (!bounds) return;
  const rect = canvas.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;
  const pad = Math.max(34, Math.min(width, height) * 0.08);
  const boundsWidth = Math.max(1, bounds.maxX - bounds.minX);
  const boundsHeight = Math.max(1, bounds.maxY - bounds.minY);
  const scale = Math.min((width - pad * 2) / boundsWidth, (height - pad * 2) / boundsHeight);
  state.view.scale = scale;
  state.view.x = width / 2 - ((bounds.minX + bounds.maxX) / 2) * scale;
  state.view.y = height / 2 - ((bounds.minY + bounds.maxY) / 2) * scale;
  draw();
}

function toScreen(point) {
  return [
    point[0] * state.view.scale + state.view.x,
    point[1] * state.view.scale + state.view.y,
  ];
}

function roadStyle(highway) {
  const styles = {
    trunk: ["#263942", 2.4],
    primary: ["#315765", 2.2],
    secondary: ["#5e7380", 1.7],
    tertiary: ["#788892", 1.3],
    residential: ["#a3abad", 0.8],
    unclassified: ["#939d9f", 0.8],
  };
  return styles[highway] || ["#99a2a4", 0.8];
}

function drawRoads() {
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const road of state.roads) {
    ctx.beginPath();
    road.points.forEach((point, index) => {
      const [x, y] = toScreen(point);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.lineWidth = Math.max(0.45, road.width / Math.max(0.75, Math.sqrt(state.view.scale)));
    ctx.strokeStyle = road.color;
    ctx.stroke();
  }
}

function drawNodes() {
  const radius = state.view.scale > 0.05 ? 2.1 : 1.4;
  if (showNodesEl.checked) {
    ctx.fillStyle = "rgba(62, 82, 86, 0.28)";
    for (const node of state.nodes) {
      if (node.hasLight) continue;
      const [x, y] = toScreen(node.point);
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  ctx.fillStyle = "#123f35";
  for (const node of state.nodes) {
    if (!node.hasLight) continue;
    const [x, y] = toScreen(node.point);
    ctx.beginPath();
    ctx.arc(x, y, radius + 2.3, 0, Math.PI * 2);
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
  }
}

function drawLights() {
  for (const light of state.lights) {
    if (!light.matched && !showUnmatchedEl.checked) continue;
    const [x, y] = toScreen(light.point);
    ctx.beginPath();
    ctx.arc(x, y, light.matched ? 5.3 : 6.2, 0, Math.PI * 2);
    ctx.fillStyle = light.matched ? "#0b8f68" : "#d44835";
    ctx.fill();
    ctx.lineWidth = 1.6;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
  }
}

function draw() {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);

  drawRoads();
  drawNodes();
  drawLights();
}

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`);
  return response.json();
}

async function loadMap() {
  const [roadnet, trafficLights] = await Promise.all([
    loadJson("data/roadnet.json"),
    loadJson("data/traffic_lights.json"),
  ]);

  const signalNodeIds = new Set();
  for (const light of trafficLights.trafficLights) {
    if (light.matched) signalNodeIds.add(light.nearestNode);
  }

  let roadBounds = null;
  state.roads = roadnet.roads.map((road) => {
    const points = road.points.map((point) => project(point.x, point.y));
    points.forEach((point) => {
      roadBounds = extendBounds(roadBounds, point);
    });
    const [color, width] = roadStyle(road.highway);
    return { points, color, width };
  });

  state.nodes = roadnet.intersections.map((intersection) => ({
    id: intersection.id,
    point: project(intersection.point.x, intersection.point.y),
    hasLight: signalNodeIds.has(intersection.id),
  }));

  let allBounds = { ...roadBounds };
  state.lights = trafficLights.trafficLights.map((light) => {
    const point = project(light.lon, light.lat);
    allBounds = extendBounds(allBounds, point);
    return {
      point,
      matched: light.matched,
      lightNumber: light.lightNumber,
      nearestNode: light.nearestNode,
    };
  });

  state.roadBounds = roadBounds;
  state.allBounds = allBounds;

  const matchedLights = state.lights.filter((light) => light.matched).length;
  nodeCountEl.textContent = roadnet.intersections.length.toLocaleString();
  roadCountEl.textContent = roadnet.roads.length.toLocaleString();
  lightCountEl.textContent = state.lights.length.toLocaleString();
  matchedCountEl.textContent = matchedLights.toLocaleString();
  signalNodeCountEl.textContent = signalNodeIds.size.toLocaleString();
  outsideCountEl.textContent = (state.lights.length - matchedLights).toLocaleString();
  statusEl.textContent = "Showing generated graph output from data/roadnet.json and data/traffic_lights.json.";
  fitBounds(state.roadBounds);
}

document.getElementById("fitRoads").addEventListener("click", () => fitBounds(state.roadBounds));
document.getElementById("fitAll").addEventListener("click", () => fitBounds(state.allBounds));
showNodesEl.addEventListener("change", draw);
showUnmatchedEl.addEventListener("change", draw);

canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mouseX = event.clientX - rect.left;
  const mouseY = event.clientY - rect.top;
  const before = [(mouseX - state.view.x) / state.view.scale, (mouseY - state.view.y) / state.view.scale];
  const factor = event.deltaY < 0 ? 1.12 : 0.9;
  state.view.scale *= factor;
  state.view.x = mouseX - before[0] * state.view.scale;
  state.view.y = mouseY - before[1] * state.view.scale;
  draw();
}, { passive: false });

canvas.addEventListener("pointerdown", (event) => {
  state.dragging = true;
  state.lastPointer = [event.clientX, event.clientY];
  canvas.classList.add("dragging");
  canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener("pointermove", (event) => {
  if (!state.dragging) return;
  const next = [event.clientX, event.clientY];
  state.view.x += next[0] - state.lastPointer[0];
  state.view.y += next[1] - state.lastPointer[1];
  state.lastPointer = next;
  draw();
});

canvas.addEventListener("pointerup", () => {
  state.dragging = false;
  canvas.classList.remove("dragging");
});

window.addEventListener("resize", () => draw());

loadMap().catch((error) => {
  console.error(error);
  statusEl.textContent = `Could not load generated graph files: ${error.message}. Run python3 idan_graph_scripts/build_beer_sheva_graph.py first.`;
});
