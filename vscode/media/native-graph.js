/* global acquireVsCodeApi, PixelNativeGraph, PixelNativeSpace */
const host = acquireVsCodeApi(), $ = id => document.getElementById(id);
const renderer = new PixelNativeGraph.NativeGraphRenderer($('canvas'));
const spaceRenderer = new PixelNativeSpace.NativeSpaceRenderer($('canvas'));
let spatialDrag;
let spatialView, selectedEntity, coordinateText = {}, coordinateChanges = new Set();
const camera = () => spatialView ? spaceRenderer : renderer;
let runtime, runtimeFrames = [], runtimeSelected;
let runtimeRequest = 0;
let expressionText = {}, initializerText = {}, creationInputs = [], creationValues = [];
let projection, graph, epoch, layouts = {}, pending = true, selected = '', graphKey = '';
const key = item => JSON.stringify([item.source, item.name]);
const positions = () => layouts[graph ? key(graph) : ''] || {};
function send(type, extra = {}) {
  const requestId = type === 'runtime-expand' || type === 'runtime-space' ? ++runtimeRequest : undefined;
  host.postMessage({ type, epoch, source: graph?.source, graph: graph?.node, ...extra, requestId });
}
function option(select, value, text) { const item = document.createElement('option'); item.value = value; item.textContent = text; select.append(item); return item; }
function members() {
  if (!graph) return [];
  return [['values', 'value'], ['children', 'instance'], ['states', 'state'], ['updates', 'next'], ['outputs', 'output']]
    .flatMap(([field, kind]) => graph[field].map(item => ({ ...item, kind, key: kind + ':' + item.node })));
}
function selectedMember() { return members().find(item => item.key === selected); }
function draw() {
  if (spatialView) spaceRenderer.draw(spatialView, selectedEntity, spatialDrag?.view || spatialView);
  else renderer.draw(graph, positions(), selected);
  $('spatial-tool').hidden = !spatialView;
  $('canvas-hint').textContent = spatialView && $('spatial-tool').value !== 'orbit' ? 'Drag an entity along the selected axis · Escape to cancel\nRelease to edit source · Restart debugging to run changes' : spatialView ? 'Drag to orbit · Scroll to zoom · Select an entity\nWorld XYZ · Independent time T · Read only' : 'Drag to orbit · Scroll to zoom\nLayout XYZ · Logical time T';
  $('canvas').setAttribute('aria-label', spatialView ? 'Native entity positions. Drag or use arrow keys to orbit; select entities in the inspector.' : 'Native flow graph. Drag or use arrow keys to rotate; select nodes in the inspector.');
}
function closeSpace() { spatialDrag = undefined; $('spatial-tool').value = 'orbit'; spatialView = undefined; selectedEntity = undefined; draw(); }
function selectEntity(id) {
  selectedEntity = id;
  const entity = spatialView?.entities.find(item => item.id === id);
  const description = $('entity-position');
  if (description) description.textContent = entity ? `#${id} · X ${entity.position[0]} · Y ${entity.position[1]} · Z ${entity.position[2]} ${spatialView.unit}` : '';
  const select = $('entity-selection'); if (select) select.value = id;
  const sourceButton = $('entity-source'), sourceNote = $('entity-source-note');
  const coordinates = entity?.origin && projection.coordinates?.find(item => item.source === entity.origin.source && item.node === entity.origin.node);
  for (const option of $('spatial-tool').options) {
    if (option.value === 'orbit') continue;
    option.disabled = pending || !coordinates || coordinates.source.startsWith('deps/') || !coordinates.axes.some(axis => axis.name === option.value && axis.literal);
  }
  if ($('spatial-tool').selectedOptions[0]?.disabled) $('spatial-tool').value = 'orbit';
  if (sourceButton) {
    sourceButton.disabled = !coordinates;
    sourceButton.onclick = () => {
      if (!coordinates) return;
      $('coordinate-source').value = JSON.stringify([coordinates.source, coordinates.node]);
      inspectCoordinates(); $('coordinate-editor').scrollIntoView({ block: 'nearest' });
    };
  }
  if (sourceNote) sourceNote.textContent = coordinates ? 'Source edits affect every execution of this position expression.' : 'Computed position or source origin unavailable.';
  draw();
}
function controls() {
  for (const input of document.querySelectorAll('aside button, aside input, aside select, aside textarea')) {
    if (input.closest('#coordinate-editor')) continue;
    if (!input.closest('#runtime')) input.disabled = pending || !graph;
  }
  coordinateControls();
  $('graph').disabled = pending || !projection?.graphs.length;
  const item = selectedMember(), movable = item && ['value', 'instance'].includes(item.kind);
  for (const id of ['x', 'y', 'z', 'move']) $(id).disabled ||= !movable;
  $('remove').disabled ||= !item || !['value', 'instance', 'state', 'next'].includes(item.kind);
  $('expression-apply').disabled ||= !item;
  $('add').disabled ||= !$('palette').value;
}
function coordinateControls() {
  const item = projection?.coordinates?.find(item => JSON.stringify([item.source, item.node]) === $('coordinate-source').value);
  $('coordinate-source').disabled = pending || !projection?.coordinates?.length;
  $('coordinate-context').textContent = item ? item.source + ' · ' + item.type + ' · Computed axes remain in source' : 'No native coordinate expressions.';
  for (const axis of ['x', 'y', 'z']) {
    const field = item?.axes.find(field => field.name === axis);
    $('coordinate-' + axis).disabled = pending || !field?.literal || item.source.startsWith('deps/');
  }
  $('coordinate-reveal').disabled = pending || !item;
  $('coordinate-apply').disabled = pending || !item || !item.axes.some(field => field.literal) || item.source.startsWith('deps/');
}
function inspectCoordinates() {
  coordinateChanges.clear();
  const details = coordinateText[$('coordinate-source').value];
  for (const axis of ['x', 'y', 'z']) {
    const input = $('coordinate-' + axis), expression = details?.axes[axis] || '';
    const value = expression ? Number(expression.replaceAll('_', '').replace(/\s/g, '')) : NaN;
    input.value = Number.isFinite(value) ? value : ''; input.title = expression;
    input.oninput = () => coordinateChanges.add(axis);
  }
  coordinateControls();
}
$('coordinate-source').onchange = inspectCoordinates;
$('coordinate-reveal').onclick = () => {
  const item = projection.coordinates.find(item => JSON.stringify([item.source, item.node]) === $('coordinate-source').value);
  if (item) send('coordinate-source', { source: item.source, node: item.node });
};
$('coordinate-apply').onclick = () => {
  const item = projection.coordinates.find(item => JSON.stringify([item.source, item.node]) === $('coordinate-source').value);
  if (!item || pending) return;
  const values = {};
  for (const axis of ['x', 'y', 'z']) { const input = $('coordinate-' + axis); if (coordinateChanges.has(axis) && !input.disabled && input.value !== '') values[axis] = input.valueAsNumber; }
  if (!Object.keys(values).length || !Object.values(values).every(Number.isFinite)) { $('status').textContent = 'Enter finite coordinates for the axes you want to change.'; return; }
  send('coordinate-edit', { source: item.source, node: item.node, values }); pending = true; controls(); coordinateControls();
};
function creationFields(id, ports, values = []) {
  const container = $(id); container.replaceChildren();
  return ports.map((port, index) => {
    const label = document.createElement('label'), input = document.createElement('textarea');
    label.textContent = port.name + ': ' + (port.type || '?');
    input.rows = 2; input.value = values[index] || ''; input.setAttribute('aria-label', 'Creation parameter ' + port.name);
    label.append(input); container.append(label); return input;
  });
}
function paletteChanged() {
  const target = graph?.palette.find(item => item.target === $('palette').value);
  creationInputs = creationFields('creation-inputs', target?.initialInputs || []); controls();
}
function inspect() {
  selected = $('selection').value;
  const item = selectedMember();
  const input = graph?.inputs.find(item => 'input:' + item.name === selected);
  const initial = graph?.initialInputs.find(item => 'initial:' + item.name === selected);
  $('signature').textContent = item ? `${item.kind} ${item.name || item.state || ''}\n${item.type || item.expression?.type || item.result || ''}${item.target ? '\n' + item.target : ''}` : initial ? 'Creation parameter · ' + initial.name + ': ' + initial.type : input ? input.name + ': ' + input.type : '';
  $('expression-editor').hidden = !item;
  $('expression-value').value = item ? expressionText[JSON.stringify([graph.source, item.node])] || '' : '';
  const target = item?.kind === 'instance' && graph.palette.find(value => value.target === item.target);
  const ports = target?.initialInputs || [];
  $('creation-editor').hidden = !ports.length;
  creationValues = creationFields('creation-values', ports, item ? initializerText[JSON.stringify([graph.source, item.node])] : []);
  if (item && ['value', 'instance'].includes(item.kind)) {
    const computations = members().filter(value => ['value', 'instance'].includes(value.kind)).sort((a, b) => a.range.start - b.range.start);
    const index = computations.findIndex(value => value.node === item.node), point = positions()[item.name] || [(index % 3) * 2 - 1, Math.floor(index / 3) * 1.7 - 1, (index % 2) * 1.5];
    ['x', 'y', 'z'].forEach((id, i) => { $(id).value = point[i]; });
  }
  controls(); draw(); renderRuntime();
}
function valueRow(container, label, value) {
  const row = document.createElement('div'), text = document.createElement('span');
  row.className = 'runtime-value'; text.textContent = label + ': ' + value.value;
  row.append(text);
  if (value.path) {
    const expand = document.createElement('button'); expand.textContent = 'Inspect';
    expand.onclick = () => send('runtime-expand', { path: value.path, start: 0 });
    row.append(expand);
    if (/^(Space|Entity)</.test(value.runtime_type || '')) {
      const spatial = document.createElement('button'); spatial.textContent = 'Space';
      spatial.onclick = () => send('runtime-space', { path: value.path, start: 0 });
      row.append(spatial);
    }
  }
  container.append(row);
}
function renderRuntime() {
  runtimeRequest++; send('runtime-cancel');
  closeSpace();
  $('runtime-values').replaceChildren();
  if (!runtime) return;
  if (runtime.source !== graph?.source || runtime.declaration !== graph?.node) {
    $('runtime-status').textContent = 'Select the declaration of the paused flow to inspect its values.';
    return;
  }
  $('runtime-status').textContent = `Time ${runtime.z} · Instance ${runtime.instance ?? 'direct'} · Step ${runtime.step ?? '—'}`;
  const container = $('runtime-values');
  for (const item of runtime.inputs) valueRow(container, 'Input ' + item.value.name, item.value);
  for (const item of runtime.values) valueRow(container, item.name + ' · ' + item.phase, item.result);
  for (const item of runtime.children) valueRow(container, 'Child ' + item.name, { value: item.phase });
  for (const item of runtime.states) {
    valueRow(container, item.name + ' · old', item.old);
    valueRow(container, item.name + (item.retained ? ' · unchanged' : item.staged ? ' · staged next' : ' · next pending'), item.next);
  }
  valueRow(container, 'Output', runtime.output);
}
function runtimePage(start) { runtimeRequest++; send('runtime-cancel'); send('runtime-select', { frame: runtimeSelected, start }); }
$('runtime-frame').onchange = () => { runtimeSelected = Number($('runtime-frame').value); runtimePage(0); };
$('runtime-prev').onclick = () => runtimePage(Math.max(0, runtime.start - runtime.count));
$('runtime-next').onclick = () => runtimePage(runtime.start + runtime.count);
function rebuild() {
  for (const id of ['selection', 'palette', 'target', 'dependencies']) $(id).replaceChildren();
  if (!graph) { $('signature').textContent = ''; controls(); draw(); return; }
  for (const item of graph.initialInputs) option($('selection'), 'initial:' + item.name, 'Creation parameter · ' + item.name);
  for (const item of graph.inputs) option($('selection'), 'input:' + item.name, 'Input · ' + item.name);
  for (const item of members()) option($('selection'), item.key, item.kind + ' · ' + (item.name || item.state || 'output'));
  if ([...$('selection').options].some(item => item.value === selected)) $('selection').value = selected;
  for (const item of graph.palette.filter(item => item.kind === 'flow' && !item.generic)) option($('palette'), item.target, item.target);
  option($('target'), JSON.stringify({ kind: 'set-output' }), 'Flow output');
  for (const item of graph.states) option($('target'), JSON.stringify({ kind: 'set-next', state: item.name }), 'next ' + item.name);
  const names = new Map([...graph.inputs, ...members()].map(item => [item.node, item.name || (item.kind === 'next' ? 'next ' + item.state : 'output')]));
  for (const item of graph.dependencies) {
    const row = document.createElement('div'); row.className = 'edge';
    row.textContent = `${names.get(item.source) || '?'} → ${names.get(item.target) || '?'}`;
    $('dependencies').append(row);
  }
  paletteChanged(); inspect();
}
function edit(action) { if (pending || !graph) return; pending = true; controls(); $('status').textContent = 'Updating source…'; send('edit', { action }); }
window.addEventListener('message', ({ data }) => {
  if (data.type === 'runtime') {
    runtime = data.view; runtimeFrames = data.frames || []; runtimeSelected = data.selected;
    $('runtime-frame').replaceChildren();
    for (const frame of runtimeFrames) option($('runtime-frame'), frame.frame, frame.name + ' · frame ' + frame.frame);
    $('runtime-frame').value = runtimeSelected; $('runtime-frame').hidden = !runtimeFrames.length;
    $('runtime-prev').hidden = !runtime || runtime.start === 0;
    $('runtime-next').hidden = !runtime || runtime.start + runtime.count >= Math.max(...Object.values(runtime.totals));
    $('runtime-status').textContent = data.message || '';
    renderRuntime();
  } else if (data.type === 'runtime-inspection-error') {
    if (!runtime || pending || data.requestId !== runtimeRequest) return;
    renderRuntime(); $('runtime-status').textContent = data.message;
  } else if (data.type === 'runtime-space') {
    if (!runtime || pending || data.requestId !== runtimeRequest) return;
    cancelSpatialDrag();
    const view = data.view, container = $('runtime-values'); container.replaceChildren();
    spatialView = view;
    const selector = document.createElement('select'); selector.id = 'entity-selection'; selector.setAttribute('aria-label', 'Spatial entity');
    for (const entity of view.entities) option(selector, entity.id, '#' + entity.id);
    const position = document.createElement('p'); position.id = 'entity-position';
    const sourceButton = document.createElement('button'); sourceButton.id = 'entity-source'; sourceButton.textContent = 'Edit position source';
    const sourceNote = document.createElement('p'); sourceNote.id = 'entity-source-note';
    selector.onchange = () => selectEntity(Number(selector.value)); container.append(selector, position, sourceButton, sourceNote);
    selectEntity(view.entities.some(entity => entity.id === selectedEntity) ? selectedEntity : view.entities[0]?.id);
    const back = document.createElement('button'); back.textContent = 'Back to flow'; back.onclick = renderRuntime; container.append(back);
    const caption = document.createElement('p'); caption.textContent = `${view.identity} · ${view.frame} · ${view.unit} · Time ${view.time} · Snapshot ${view.version ?? 'entity'} · ${view.total} entities`; container.append(caption);
    const linkCaption = document.createElement('p'); linkCaption.textContent = `${view.links?.length || 0} of ${view.visibleLinkTotal || 0} links between visible entities · ${view.linkTotal || 0} links in this snapshot`; container.append(linkCaption);
    for (const link of view.links || []) valueRow(container, `Link #${link.id} · #${link.source} → #${link.target}`, link.payload);
    for (const entity of view.entities) {
      const position = document.createElement('p'); position.textContent = `#${entity.id} · X ${entity.position[0]} · Y ${entity.position[1]} · Z ${entity.position[2]}` + (entity.distance === null ? '' : ` · Distance ${entity.distance}`); container.append(position);
      valueRow(container, 'Payload', entity.payload);
    }
    const page = start => send('runtime-space', { path: view.path, start, neighbors: data.neighbors });
    if (view.start > 0) { const prev = document.createElement('button'); prev.textContent = 'Previous entities'; prev.onclick = () => page(Math.max(0, view.start - view.count)); container.append(prev); }
    if (view.start + view.count < view.total) { const next = document.createElement('button'); next.textContent = 'More entities'; next.onclick = () => page(view.start + view.count); container.append(next); }
    if (view.version !== null) {
      const origin = document.createElement('input'), radius = document.createElement('input');
      origin.type = radius.type = 'number'; origin.min = radius.min = '0'; origin.step = '1'; radius.step = 'any';
      origin.setAttribute('aria-label', 'Origin entity ID'); origin.placeholder = 'Origin entity ID'; origin.value = data.neighbors?.origin ?? '';
      radius.setAttribute('aria-label', 'Radius in ' + view.unit); radius.placeholder = 'Radius in ' + view.unit; radius.value = data.neighbors?.radius ?? '';
      const query = document.createElement('button'); query.textContent = 'Find neighbors';
      query.onclick = () => { if (Number.isInteger(origin.valueAsNumber) && origin.valueAsNumber >= 0 && Number.isFinite(radius.valueAsNumber) && radius.valueAsNumber >= 0) send('runtime-space', { path: view.path, start: 0, neighbors: { origin: origin.valueAsNumber, radius: radius.valueAsNumber } }); };
      const all = document.createElement('button'); all.textContent = 'All entities'; all.onclick = () => send('runtime-space', { path: view.path, start: 0 });
      container.append(origin, radius, query, all);
    }
  } else if (data.type === 'runtime-children') {
    if (!runtime || pending || data.requestId !== runtimeRequest) return;
    const container = $('runtime-values'); container.replaceChildren();
    const back = document.createElement('button'); back.textContent = 'Back to flow'; back.onclick = renderRuntime; container.append(back);
    for (const item of data.variables) valueRow(container, item.name, item);
    if (data.start > 0) { const previous = document.createElement('button'); previous.textContent = 'Previous values'; previous.onclick = () => send('runtime-expand', { path: data.path, start: Math.max(0, data.start - 50) }); container.append(previous); }
    if (data.variables.length === 50) { const next = document.createElement('button'); next.textContent = 'More values'; next.onclick = () => send('runtime-expand', { path: data.path, start: data.start + 50 }); container.append(next); }
  } else if (data.type === 'pending') { runtime = undefined; renderRuntime(); $('runtime-frame').hidden = true; $('runtime-prev').hidden = true; $('runtime-next').hidden = true; $('runtime-status').textContent = 'Source changed; checking current program…'; pending = true; controls(); $('status').textContent = 'Checking current source…'; }
  else if (data.type === 'state') {
    initializerText = data.initializerText || {}; expressionText = data.expressionText || {}; projection = data.projection; epoch = data.epoch; pending = false; coordinateText = data.coordinateText || {};
    $('coordinate-source').replaceChildren();
    for (const item of projection.coordinates || []) option($('coordinate-source'), JSON.stringify([item.source, item.node]), item.source + ':' + (coordinateText[JSON.stringify([item.source, item.node])]?.line || '?') + ' · ' + item.type);
    inspectCoordinates();
    $('graph').replaceChildren();
    for (const item of projection.graphs) option($('graph'), key(item), item.name + ' · ' + item.source);
    graph = projection.graphs.find(item => key(item) === graphKey) || projection.graphs[0]; graphKey = graph ? key(graph) : ''; $('graph').value = graphKey;
    $('diagnostics').replaceChildren();
    for (const item of projection.diagnostics) { const row = document.createElement('p'); row.textContent = `${item.source}:${item.line}:${item.column} · ${item.message}`; $('diagnostics').append(row); }
    $('status').textContent = graph ? `${graph.values.length} computations · ${graph.children.length} child instances · ${graph.dependencies.length} dependencies · ${projection.diagnostics.length} diagnostics` : projection.coordinates?.length ? `${projection.coordinates.length} native coordinate expressions · ${projection.diagnostics.length} diagnostics` : 'No native flow or coordinate expressions available. Check compiler diagnostics.';
    rebuild();
  } else if (data.type === 'layouts') { layouts = data.layouts; inspect(); }
  else if (data.type === 'error') { pending = !data.retain; controls(); $('status').textContent = data.message; }
});
$('graph').onchange = () => { closeSpace(); graphKey = $('graph').value; graph = projection.graphs.find(item => key(item) === graphKey); selected = ''; rebuild(); };
$('selection').onchange = inspect;
$('palette').onchange = paletteChanged;
$('refresh').onclick = () => send('refresh');
$('source').onclick = () => {
  const item = selectedMember() || graph.inputs.find(item => 'input:' + item.name === selected) || graph.initialInputs.find(item => 'initial:' + item.name === selected);
  send('source', { node: item?.node });
};
$('refresh').onclick = () => send('refresh');
$('add').onclick = () => edit({ kind: 'add-instance', name: $('name').value.trim(), target: $('palette').value, initializers: creationInputs.map(input => input.value) });
$('add-value').onclick = () => edit({ kind: 'add-value', name: $('value-name').value.trim(), expression: $('value-expression').value });
$('expression-apply').onclick = () => { const item = selectedMember(); if (item) edit({ kind: 'set-expression', node: item.node, expression: $('expression-value').value }); };
$('creation-apply').onclick = () => { const item = selectedMember(); if (item?.kind === 'instance') edit({ kind: 'set-initializers', node: item.node, initializers: creationValues.map(input => input.value) }); };
$('add-state').onclick = () => edit({ kind: 'add-state', name: $('state-name').value.trim(), type: $('state-type').value, initializer: $('state-initializer').value });
$('remove').onclick = () => { const item = selectedMember(); if (item && ['value', 'instance', 'state', 'next'].includes(item.kind)) edit({ kind: 'remove-' + item.kind, node: item.node }); };
$('set-result').onclick = () => { if ($('target').value) edit({ ...JSON.parse($('target').value), expression: $('transition-expression').value }); };
$('move').onclick = () => send('layout', { name: selectedMember()?.name, position: ['x', 'y', 'z'].map(id => $(id).valueAsNumber) });
$('reset').onclick = () => { renderer.yaw = -.5; renderer.pitch = .5; renderer.zoom = 1; spaceRenderer.yaw = -.5; spaceRenderer.pitch = .5; spaceRenderer.zoom = 1; draw(); };
let drag;
function cancelSpatialDrag() {
  if (spatialDrag) { spatialView = spatialDrag.view; spatialDrag = undefined; selectEntity(selectedEntity); }
  drag = undefined;
}
$('spatial-tool').onchange = () => { cancelSpatialDrag(); draw(); };
$('canvas').onpointerdown = event => {
  if (pending) return;
  const tool = $('spatial-tool').value;
  if (spatialView && tool !== 'orbit') {
    const rect = $('canvas').getBoundingClientRect(), entity = spaceRenderer.pick(event.clientX - rect.left, event.clientY - rect.top);
    if (!entity) return;
    selectEntity(entity.id);
    if ($('spatial-tool').value !== tool) return;
    const coordinates = projection.coordinates.find(item => item.source === entity.origin.source && item.node === entity.origin.node);
    spatialDrag = { view: spatialView, coordinates, axis: tool, id: entity.id, epoch, x: event.clientX, y: event.clientY, width: rect.width, height: rect.height, yaw: spaceRenderer.yaw, pitch: spaceRenderer.pitch, zoom: spaceRenderer.zoom };
  } else drag = { x: event.clientX, y: event.clientY, total: 0 };
  $('canvas').setPointerCapture(event.pointerId);
};
$('canvas').onpointermove = event => {
  if (spatialDrag) {
    const d = spatialDrag;
    try {
      d.value = PixelNativeSpace.dragCoordinate(d.view, d.width, d.height, d.yaw, d.pitch, d.zoom, d.id, d.axis, event.clientX - d.x, event.clientY - d.y);
      spatialView = { ...d.view, entities: d.view.entities.map(entity => entity.id !== d.id ? entity : { ...entity, position: entity.position.map((value, i) => i === ['x', 'y', 'z'].indexOf(d.axis) ? d.value : value) }) };
      selectEntity(d.id);
    } catch (error) { cancelSpatialDrag(); $('status').textContent = error.message; }
    return;
  }
  if (!drag) return;
  const dx = event.clientX - drag.x, dy = event.clientY - drag.y; drag.total += Math.abs(dx) + Math.abs(dy); camera().yaw += dx * .008; camera().pitch = Math.max(-1.3, Math.min(1.3, camera().pitch + dy * .008)); drag.x = event.clientX; drag.y = event.clientY; draw();
};
$('canvas').onpointerup = event => {
  if (spatialDrag) {
    const d = spatialDrag; cancelSpatialDrag();
    const original = d.view.entities.find(entity => entity.id === d.id).position[['x', 'y', 'z'].indexOf(d.axis)];
    if (!pending && epoch === d.epoch && Number.isFinite(d.value) && d.value !== original) {
      send('coordinate-edit', { source: d.coordinates.source, node: d.coordinates.node, values: { [d.axis]: d.value } });
      pending = true; controls(); $('status').textContent = 'Updating position source…'; closeSpace();
    }
    return;
  }
  if (drag?.total < 5) { const rect = $('canvas').getBoundingClientRect(), item = camera().pick(event.clientX - rect.left, event.clientY - rect.top); if (item) { if (spatialView) selectEntity(item.id); else { $('selection').value = item.key; inspect(); } } } drag = undefined;
};
$('canvas').onpointercancel = cancelSpatialDrag;
$('canvas').addEventListener('wheel', event => { event.preventDefault(); if (spatialDrag) return; camera().zoom = Math.max(.25, Math.min(3, camera().zoom * Math.exp(-event.deltaY * .001))); draw(); }, { passive: false });
$('canvas').onkeydown = event => { if (event.key === 'Escape') { cancelSpatialDrag(); return; } if (spatialDrag) return; const actions = { ArrowLeft: () => camera().yaw -= .1, ArrowRight: () => camera().yaw += .1, ArrowUp: () => camera().pitch = Math.min(1.3, camera().pitch + .1), ArrowDown: () => camera().pitch = Math.max(-1.3, camera().pitch - .1), '+': () => camera().zoom = Math.min(3, camera().zoom * 1.1), '-': () => camera().zoom = Math.max(.25, camera().zoom / 1.1) }; if (actions[event.key]) { event.preventDefault(); actions[event.key](); draw(); } };
new ResizeObserver(draw).observe($('canvas'));
controls(); send('ready');
