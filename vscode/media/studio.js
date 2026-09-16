const vscode = acquireVsCodeApi(),
  $ = (id) => document.getElementById(id),
  canvas = $("space"),
  ctx = canvas.getContext("2d");
let data,
  irRequestedSession,
  angle = -0.65,
  pitch = 0.45,
  zoom = 1,
  drag,
  last,
  projected = [],
  mode = "time",
  replaying = false,
  timer,
  waiting = false;
const send = (type, extra = {}) => vscode.postMessage({ type, ...extra });
function requestIR() {
  if (!$("ir-panel").open || !data?.session || data.inspection?.ir || irRequestedSession === data.session) return;
  irRequestedSession = data.session;
  $("ir").textContent = "Loading IR…";
  send("inspect-ir", { session: data.session });
}
$("ir-panel").ontoggle = () => {
  if (!$("ir-panel").open) irRequestedSession = undefined;
  else requestIR();
};
for (const id of [
  "build",
  "run",
  "pause",
  "cancel",
  "step",
  "export",
  "record",
  "reveal",
])
  $(id).onclick = () => send(id);
$("input").onchange = () => send("input", { value: $("input").value });
$("time").oninput = () => {
  replaying = false;
  seek(Number($("time").value));
};
$("back").onclick = () => seek(Math.max(0, (data?.cursor || 0) - 1));
$("forward").onclick = () =>
  seek(Math.min(data?.length || 0, (data?.cursor || 0) + 1));
$("play").onclick = () => {
  if (!data) return;
  replaying = !replaying;
  if (replaying && data?.cursor === data?.length) seek(0);
  play();
};
function play() {
  clearTimeout(timer);
  $("play").textContent = replaying ? "Ⅱ Replay" : "▷ Replay";
  if (replaying && !waiting) {
    if (data.cursor >= data.length) {
      replaying = false;
      play();
      return;
    }
    timer = setTimeout(() => seek(data.cursor + 1), 120);
  }
}
function seek(z) {
  if (!data) return;
  waiting = true;
  send("seek", { z });
}
function requestIR() {
  if (!$("ir-panel").open || !data?.session || data.inspection?.ir || irRequestedSession === data.session) return;
  irRequestedSession = data.session;
  $("ir").textContent = "Loading IR…";
  send("inspect-ir", { session: data.session });
}
$("ir-panel").ontoggle = () => {
  if (!$("ir-panel").open) irRequestedSession = undefined;
  else requestIR();
};
for (const id of ["time", "image", "spatial"])
  $(id + "-view").onclick = () => {
    mode = id;
    canvas.hidden = id !== "time";
    $("image").hidden = id !== "image";
    $("spatial-editor").hidden = id !== "spatial";
    canvas.parentElement.hidden = id === "spatial";
    $("axes").hidden = id !== "time";
    $("time-view").setAttribute("aria-pressed", id === "time");
    $("image-view").setAttribute("aria-pressed", id === "image");
    $("spatial-view").setAttribute("aria-pressed", id === "spatial");
    if (id === "spatial") send("spatial-open");
    draw();
  };
window.addEventListener("message", (event) => {
  const message = event.data;
  if (message.type === "input") $("input").value = message.value;
  if (message.type === "state") {
    data = message.data;
    waiting = false;
    $("empty").hidden = true;
    $("error").hidden = !data.state.error;
    $("error").textContent = data.state.error?.message || "";
    $("time").max = data.length;
    $("time").value = data.cursor;
    $("time-label").textContent = `Z ${data.cursor} / ${data.length}`;
    $("output").textContent =
      data.output.join("\n") ||
      (data.state.halted ? "No output." : "Ready to run.");
    $("state").textContent = data.state_text;
    if (data.inspection?.ir) $("ir").textContent = JSON.stringify(data.inspection.ir, null, 2);
    else if (irRequestedSession !== data.session) $("ir").textContent = "Open to inspect compiler IR.";
    requestIR();
    if (data.image) $("image").src = "data:image/png;base64," + data.image;
    $("status").textContent =
      `${data.state.halted ? "Halted" : "Paused"} · ${data.event?.op || "Initial state"} · ${data.volume.voxels.length} voxels in time window`;
    draw();
    play();
  }
  if (message.type === "ir" && message.session === data?.session) {
    data.inspection = { ...data.inspection, ir: message.ir };
    $("ir").textContent = JSON.stringify(message.ir, null, 2);
  }
  if (message.type === "ir-error" && message.session === data?.session) {
    $("ir").textContent = message.message;
  }
  if (message.type === "error") {
    $("error").hidden = false;
    $("error").textContent = message.message;
    waiting = false;
    replaying = false;
    play();
  }
  if (message.type === "stale") {
    data = undefined;
    irRequestedSession = undefined;
    $("ir").textContent = "Rebuild to inspect the updated program.";
    replaying = false;
    clearTimeout(timer);
    $("status").textContent = "Source or input changed · rebuild to refresh";
    $("empty").hidden = false;
    $("empty").textContent = "Rebuild to explore the updated program.";
    draw();
  }
  if (message.type === "running") {
    $("run").disabled = message.value;
    $("build").disabled = message.value;
    $("step").disabled = message.value;
  }
});
function transform(x, y, z) {
  const xx = x * Math.cos(angle) + z * Math.sin(angle),
    zz = -x * Math.sin(angle) + z * Math.cos(angle);
  return [
    xx,
    y * Math.cos(pitch) - zz * Math.sin(pitch),
    y * Math.sin(pitch) + zz * Math.cos(pitch),
  ];
}
function draw() {
  const dpr = devicePixelRatio || 1,
    w = canvas.clientWidth,
    h = canvas.clientHeight;
  if (!w || !h) return;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  projected = [];
  const voxels = data?.volume.voxels || [];
  if (!voxels.length) return;
  const modules = [...new Set(voxels.map((v) => v.module))].sort();
  let maxX = 0,
    maxY = 0;
  for (const v of voxels) {
    maxX = Math.max(maxX, v.position[0]);
    maxY = Math.max(maxY, v.position[1]);
  }
  const stride = maxX + 8,
    width = stride * modules.length;
  const z0 = data.volume.range[0],
    depth = Math.max(1, data.volume.range[1] - z0);
  const raw = (x, y, z) => transform(x, y, (z - z0) * 0.35);
  let lowX = Infinity,
    highX = -Infinity,
    lowY = Infinity,
    highY = -Infinity;
  for (const v of voxels) {
    const p = raw(
      v.position[0] + modules.indexOf(v.module) * stride,
      v.position[1],
      v.position[2],
    );
    lowX = Math.min(lowX, p[0]);
    highX = Math.max(highX, p[0]);
    lowY = Math.min(lowY, p[1]);
    highY = Math.max(highY, p[1]);
  }
  const scale =
    Math.min(
      (w - 64) / Math.max(10, highX - lowX),
      (h - 72) / Math.max(10, highY - lowY),
    ) * zoom;
  function screen(x, y, z) {
    const p = raw(x, y, z);
    return [
      w / 2 + (p[0] - (lowX + highX) / 2) * scale,
      h / 2 + (p[1] - (lowY + highY) / 2) * scale,
      p[2],
    ];
  }
  ctx.strokeStyle = getComputedStyle(document.body).color;
  ctx.globalAlpha = 0.18;
  ctx.lineWidth = 1;
  for (const [a, b] of [
    [
      [0, 0, z0],
      [width, 0, z0],
    ],
    [
      [0, 0, z0],
      [0, maxY + 5, z0],
    ],
    [
      [0, 0, z0],
      [0, 0, z0 + depth],
    ],
  ]) {
    const p = screen(...a),
      q = screen(...b);
    ctx.beginPath();
    ctx.moveTo(p[0], p[1]);
    ctx.lineTo(q[0], q[1]);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  for (const v of voxels) {
    const [x, y, z] = v.position,
      p = screen(x + modules.indexOf(v.module) * stride, y, z);
    projected.push({ p, v });
  }
  projected.sort((a, b) => a.p[2] - b.p[2]);
  for (const { p, v } of projected) {
    const active = v.position[2] === data.cursor;
    const [r, g, b] = v.rgba;
    const color = {
      16: [177, 220, 110],
      17: [192, 171, 230],
      18: [178, 220, 110],
      19: [230, 181, 127],
      32: [80, 195, 222],
      48: [240, 175, 94],
      64: [222, 125, 112],
      80: [116, 135, 155],
      96: [154, 170, 220],
      112: [208, 190, 146],
      128: [92, 218, 170],
    }[r] || [r, g, b];
    const size = Math.max(2, Math.min(8, scale * 0.8)) * (active ? 1.7 : 1);
    ctx.globalAlpha = active ? 1 : 0.35;
    ctx.fillStyle = `rgb(${color[0]},${g ^ color[1]},${b ^ color[2]})`;
    ctx.fillRect(p[0] - size / 2, p[1] - size / 2, size, size);
    if (active) {
      ctx.strokeStyle = getComputedStyle(document.body).color;
      ctx.strokeRect(
        p[0] - size / 2 - 1,
        p[1] - size / 2 - 1,
        size + 2,
        size + 2,
      );
    }
  }
  ctx.globalAlpha = 1;
}
canvas.onpointerdown = (e) => {
  drag = { x: e.clientX, y: e.clientY, moved: false };
  last = { x: e.clientX, y: e.clientY };
  canvas.setPointerCapture(e.pointerId);
};
canvas.onpointermove = (e) => {
  if (!drag) return;
  const dx = e.clientX - last.x,
    dy = e.clientY - last.y;
  if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 4)
    drag.moved = true;
  angle += dx * 0.008;
  pitch = Math.max(-1.4, Math.min(1.4, pitch + dy * 0.008));
  last = { x: e.clientX, y: e.clientY };
  draw();
};
canvas.onpointerup = (e) => {
  if (drag && !drag.moved) {
    const bounds = canvas.getBoundingClientRect(),
      x = e.clientX - bounds.left,
      y = e.clientY - bounds.top;
    let nearest,
      distance = 12;
    for (const point of projected) {
      const d = Math.hypot(point.p[0] - x, point.p[1] - y);
      if (d < distance) {
        nearest = point;
        distance = d;
      }
    }
    if (nearest) seek(nearest.v.position[2]);
  }
  drag = null;
};
canvas.onwheel = (e) => {
  e.preventDefault();
  zoom = Math.max(0.3, Math.min(6, zoom * Math.exp(-e.deltaY * 0.001)));
  draw();
};
canvas.onkeydown = (e) => {
  if (e.key === "ArrowLeft") $("back").click();
  if (e.key === "ArrowRight") $("forward").click();
};
let spatialData, selectedSpatial;
function selectSpatial(id, reveal = false) {
  const target = spatialData?.targets[id];
  if (!target) return;
  selectedSpatial = id;
  $("spatial-target").value = id;
  $("spatial-explanation").textContent = `${target.kind} · ${target.span.source}:${target.span.start.line}\n${target.explanation}\n\n${target.text}`;
  $("spatial-value").value = target.kind === "literal" ? target.text.trim() : "";
  for (const action of ["literal", "operator", "up", "down"])
    $("spatial-" + action).disabled = !target.actions.includes(action);
  for (const button of $("spatial-grid").querySelectorAll("button[data-targets]"))
    button.setAttribute("aria-pressed", JSON.parse(button.dataset.targets).includes(id));
  const pixel = $("spatial-grid").querySelector('button[aria-pressed="true"]');
  pixel?.scrollIntoView({ block: "nearest", inline: "nearest" });
  if (reveal) send("spatial-select", { target: id });
}
function renderSpatial(view) {
  spatialData = view;
  $("spatial-error").textContent = "";
  $("spatial-module").replaceChildren(...view.sources.map(name => new Option(name, name)));
  $("spatial-module").value = view.source;
  $("spatial-target").replaceChildren(...Object.values(view.targets).map(t =>
    new Option(`${t.kind} · ${t.text.trim().slice(0, 65)}`, t.id)));
  const rows = view.regions.map(region => {
    const row = document.createElement("div");
    row.className = "spatial-row";
    row.style.width = (Math.max(...region.tokens.map(t => t.position[0])) * 10 + 26) + "px";
    for (const token of region.tokens) {
      const button = document.createElement("button");
      button.className = "semantic-pixel";
      button.style.left = token.position[0] * 10 + "px";
      button.style.backgroundColor = token.color;
      button.title = `${token.kind}: ${token.label}`;
      button.setAttribute("aria-label", button.title);
      button.dataset.targets = JSON.stringify(token.targets);
      button.disabled = !token.targets.length;
      button.onclick = () => selectSpatial(token.targets[0], true);
      row.append(button);
    }
    return row;
  });
  $("spatial-grid").replaceChildren(...rows);
  const selection = view.selection;
  const matches = selection ? Object.values(view.targets).filter(t =>
    t.span.start.offset <= selection.offset && selection.offset < t.span.end.offset && selection.end <= t.span.end.offset) : [];
  matches.sort((a, b) => (a.span.end.offset - a.span.start.offset) - (b.span.end.offset - b.span.start.offset));
  selectSpatial(matches[0]?.id || Object.keys(view.targets)[0]);
}
$("spatial-module").onchange = () => send("spatial-open", { source: $("spatial-module").value });
$("spatial-refresh").onclick = () => send("spatial-open", { source: $("spatial-module").value || undefined });
$("spatial-target").onchange = () => selectSpatial($("spatial-target").value, true);
for (const action of ["literal", "operator", "up", "down"])
  $("spatial-" + action).onclick = () => send("spatial-edit", {
    revision: spatialData?.revision, target: selectedSpatial, action, value: $("spatial-value").value,
  });
window.addEventListener("message", event => {
  const message = event.data;
  if (message.type === "spatial-state") renderSpatial(message.data);
  if (message.type === "spatial-error") $("spatial-error").textContent = message.message;
  if (message.type === "stale") {
    $("spatial-error").textContent = "Source changed. Refresh to edit the current program.";
    for (const action of ["literal", "operator", "up", "down"]) $("spatial-" + action).disabled = true;
  }
  if (message.type === "spatial-selection" && message.source === spatialData?.source) {
    const matches = Object.values(spatialData.targets).filter(t => t.span.start.offset <= message.offset && message.offset < t.span.end.offset && (message.end ?? message.offset) <= t.span.end.offset);
    matches.sort((a, b) => (a.span.end.offset - a.span.start.offset) - (b.span.end.offset - b.span.start.offset));
    if (matches.length) selectSpatial(matches[0].id);
  }
});
new ResizeObserver(draw).observe(canvas);
send("ready");
