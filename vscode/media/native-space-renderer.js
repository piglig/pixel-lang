/* Native coordinates are immutable; normalization below is only a camera transform. */
(function(root) {
  function project(view, width, height, yaw = -.5, pitch = .4, zoom = 1, framing = view) {
    const entities = view.entities;
    let magnitude = 1;
    for (const entity of framing.entities) for (const coordinate of entity.position) magnitude = Math.max(magnitude, Math.abs(coordinate));
    const normalized = framing.entities.map(entity => entity.position.map(value => value / magnitude));
    const center = [0, 1, 2].map(axis => normalized.length ? Math.min(...normalized.map(p => p[axis])) / 2 + Math.max(...normalized.map(p => p[axis])) / 2 : 0);
    let extent = 0;
    for (const position of normalized) for (let axis = 0; axis < 3; axis++) extent = Math.max(extent, Math.abs(position[axis] - center[axis]));
    extent ||= 1;
    const scale = Math.max(1, Math.min(width, height) * .32) * zoom;
    const rotate = ([x, y, z]) => {
      const horizontal = x * Math.cos(yaw) - z * Math.sin(yaw), depth = x * Math.sin(yaw) + z * Math.cos(yaw);
      return { x: horizontal, y: -(y * Math.cos(pitch) - depth * Math.sin(pitch)), depth: y * Math.sin(pitch) + depth * Math.cos(pitch) };
    };
    const items = entities.map((entity, index) => {
      const point = rotate(entity.position.map((value, axis) => (value / magnitude - center[axis]) / extent));
      return { ...entity, x: width / 2 + point.x * scale, y: height / 2 + point.y * scale, depth: point.depth };
    }).sort((a, b) => a.depth - b.depth);
    const axes = [[1, 0, 0], [0, 1, 0], [0, 0, 1]].map((v, i) => ({ ...rotate(v), label: ['X', 'Y', 'Z'][i] }));
    return { items, axes, magnitude, extent, scale };
  }
  function dragCoordinate(view, width, height, yaw, pitch, zoom, id, axis, dx, dy) {
    const index = ['x', 'y', 'z'].indexOf(axis), entity = view.entities.find(item => item.id === id);
    if (index < 0 || !entity || !Number.isFinite(dx) || !Number.isFinite(dy)) throw Error('Invalid coordinate drag.');
    const geometry = project(view, width, height, yaw, pitch, zoom), direction = geometry.axes[index];
    const length2 = direction.x ** 2 + direction.y ** 2;
    if (length2 < .01) throw Error('Rotate the view to see this axis before dragging.');
    const delta = (dx * direction.x + dy * direction.y) / length2 / geometry.scale * geometry.extent * geometry.magnitude;
    const value = entity.position[index] + delta;
    if (!Number.isFinite(value)) throw Error('Coordinate exceeds the finite numeric range.');
    return value;
  }
  function pick(items, x, y) { return items.slice().reverse().find(item => Math.hypot(item.x - x, item.y - y) <= 12); }
  function linkPaths(items, links) {
    const vertices = new Map(items.map(item => [item.id, item])), groups = new Map(), paths = [];
    for (const link of links || []) {
      if (!vertices.has(link.source) || !vertices.has(link.target)) continue;
      const key = JSON.stringify([Math.min(link.source, link.target), Math.max(link.source, link.target)]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(link);
    }
    for (const group of groups.values()) group.sort((a, b) => a.id - b.id).forEach((link, index) => {
      const a = vertices.get(link.source), b = vertices.get(link.target), length = Math.hypot(b.x - a.x, b.y - a.y);
      let from, to, c1, c2;
      if (length < 1) {
        const radius = 25 + index * 14;
        from = { x: a.x - 5, y: a.y - 6 }; to = { x: b.x + 5, y: b.y - 6 };
        c1 = { x: a.x - radius, y: a.y - radius * 2 }; c2 = { x: b.x + radius, y: b.y - radius * 2 };
      } else {
        const sign = link.source < link.target ? 1 : -1, offset = (index - (group.length - 1) / 2) * 28;
        const control = { x: (a.x + b.x) / 2 - (b.y - a.y) / length * sign * offset,
          y: (a.y + b.y) / 2 + (b.x - a.x) / length * sign * offset };
        const inset = (point, toward) => { const d = Math.hypot(toward.x - point.x, toward.y - point.y) || 1; return { x: point.x + (toward.x - point.x) / d * Math.min(9, length / 3), y: point.y + (toward.y - point.y) / d * Math.min(9, length / 3) }; };
        from = inset(a, control); to = inset(b, control);
        c1 = { x: from.x + (control.x - from.x) * 2 / 3, y: from.y + (control.y - from.y) * 2 / 3 };
        c2 = { x: to.x + (control.x - to.x) * 2 / 3, y: to.y + (control.y - to.y) * 2 / 3 };
      }
      paths.push({ id: link.id, from, to, c1, c2 });
    });
    return paths;
  }
  class NativeSpaceRenderer {
    constructor(canvas) { this.canvas = canvas; this.yaw = -.5; this.pitch = .4; this.zoom = 1; this.items = []; }
    draw(view, selected, framing = view, cameraOnly = false) {
      const { width, height } = this.canvas.getBoundingClientRect(), ratio = globalThis.devicePixelRatio || 1;
      this.canvas.width = Math.max(1, width * ratio); this.canvas.height = Math.max(1, height * ratio);
      const ctx = this.canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, width, height);
      const geometry = project(view, width, height, this.yaw, this.pitch, this.zoom, framing); this.items = geometry.items;
      const style = getComputedStyle(this.canvas), accent = style.getPropertyValue('--accent').trim() || '#79b8ff';
      ctx.font = '12px system-ui'; ctx.fillStyle = style.color;
      ctx.fillText(`${framing !== view && !cameraOnly ? 'Position preview · ' : ''}${view.identity} · ${view.unit} · Time ${view.time} · Snapshot ${view.version ?? 'entity'}`, 20, 72, width - 40);
      const first = view.entities.length ? view.start + 1 : 0, last = view.start + view.entities.length;
      ctx.fillText(`Entities ${first}–${last} of ${view.total} · ${cameraOnly ? 'Fixed camera' : 'View fits this page'}`, 20, 94, width - 40);
      if (!view.entities.length) ctx.fillText('No entities in this view', width / 2 - 65, height / 2);
      ctx.strokeStyle = accent; ctx.lineWidth = 1.5;
      for (const edge of linkPaths(geometry.items, view.links)) {
        ctx.beginPath(); ctx.moveTo(edge.from.x, edge.from.y);
        ctx.bezierCurveTo(edge.c1.x, edge.c1.y, edge.c2.x, edge.c2.y, edge.to.x, edge.to.y); ctx.stroke();
        const angle = Math.atan2(edge.to.y - edge.c2.y, edge.to.x - edge.c2.x);
        ctx.beginPath(); ctx.moveTo(edge.to.x, edge.to.y);
        ctx.lineTo(edge.to.x - Math.cos(angle - .45) * 8, edge.to.y - Math.sin(angle - .45) * 8);
        ctx.moveTo(edge.to.x, edge.to.y); ctx.lineTo(edge.to.x - Math.cos(angle + .45) * 8, edge.to.y - Math.sin(angle + .45) * 8); ctx.stroke();
      }
      for (const item of geometry.items) {
        ctx.beginPath(); ctx.arc(item.x, item.y, item.id === selected ? 8 : 5, 0, Math.PI * 2);
        ctx.fillStyle = accent; ctx.fill();
        if (item.id === selected) { ctx.strokeStyle = style.color; ctx.lineWidth = 2; ctx.stroke(); }
        ctx.fillStyle = style.color; ctx.fillText('#' + item.id, item.x + 12, item.y - 10);
      }
      for (const axis of geometry.axes) {
        const x = 56, y = height - 96;
        ctx.strokeStyle = style.color; ctx.globalAlpha = .65; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + axis.x * 30, y + axis.y * 30); ctx.stroke();
        ctx.fillText(axis.label, x + axis.x * 40, y + axis.y * 40);
      }
      ctx.globalAlpha = 1;
    }
    pick(x, y) { return pick(this.items, x, y); }
  }
  const exported = { project, pick, dragCoordinate, linkPaths, NativeSpaceRenderer };
  if (typeof module !== 'undefined') module.exports = exported; else root.PixelNativeSpace = exported;
})(globalThis);
