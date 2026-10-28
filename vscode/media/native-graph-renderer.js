(function(root) {
  function scene(graph, positions, width, height, yaw = -.5, pitch = .5, zoom = 1) {
    const scale = Math.min(width / 11, height / 8) * zoom;
    const project = ([x, y, z]) => {
      const a = x * Math.cos(yaw) - z * Math.sin(yaw), depth = x * Math.sin(yaw) + z * Math.cos(yaw);
      return { x: width / 2 + a * scale, y: height / 2 + (y * Math.cos(pitch) - depth * Math.sin(pitch)) * scale, depth: y * Math.sin(pitch) + depth * Math.cos(pitch) };
    };
    const computations = [
      ...graph.values.map(item => ({ ...item, key: 'value:' + item.node, kind: 'value' })),
      ...graph.children.map(item => ({ ...item, key: 'instance:' + item.node, kind: 'instance' })),
    ].sort((a, b) => a.range.start - b.range.start);
    const items = [
      ...graph.initialInputs.map((item, i) => ({ ...item, key: 'initial:' + item.name, kind: 'creation input', position: [-4, i * 1.2 - 3, -2] })),
      ...graph.inputs.map((item, i) => ({ ...item, key: 'input:' + item.name, kind: 'input', position: [-4, i * 1.2 - 1, 0] })),
      ...graph.states.map((item, i) => ({ ...item, key: 'state:' + item.node, kind: 'state', position: [-3, i * 1.4 + 2.4, 0] })),
      ...computations.map((item, i) => ({ ...item, position: positions[item.name] || [(i % 3) * 2 - 1, Math.floor(i / 3) * 1.7 - 1, (i % 2) * 1.5] })),
      ...graph.updates.map((item, i) => ({ ...item, name: 'next ' + item.state, key: 'next:' + item.node, kind: 'next', position: [3, i * 1.4 + 2.4, 0] })),
      ...graph.outputs.map((item, i) => ({ ...item, name: 'output', key: 'output:' + item.node, kind: 'output', position: [4, i * 1.4, 0] })),
    ];
    const projected = items.map(item => ({ ...item, ...project(item.position) }));
    const byId = new Map(projected.map(item => [item.node, item]));
    const edges = graph.dependencies.map(item => ({ node: item.reference, from: byId.get(item.source), to: byId.get(item.target), label: 'uses', kind: 'dependency' }));
    for (const item of graph.updates) edges.push({ node: item.node, from: byId.get(item.node), to: projected.find(value => value.kind === 'state' && value.name === item.state), label: 'commit', kind: 'next' });
    return { items: projected.sort((a, b) => a.depth - b.depth), edges, axes: [[[-4, 3, 0], [-2, 3, 0], 'X'], [[-4, 3, 0], [-4, 1, 0], '−Y'], [[-4, 3, 0], [-4, 3, 2], 'Z']].map(([a, b, name]) => ({ from: project(a), to: project(b), name })) };
  }
  class NativeGraphRenderer {
    constructor(canvas) { this.canvas = canvas; this.yaw = -.5; this.pitch = .5; this.zoom = 1; }
    draw(graph, positions = {}, selected) {
      const { width, height } = this.canvas.getBoundingClientRect(), ratio = globalThis.devicePixelRatio || 1;
      this.canvas.width = Math.max(1, width * ratio); this.canvas.height = Math.max(1, height * ratio);
      const ctx = this.canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, width, height);
      if (!graph) { this.geometry = null; return; }
      this.geometry = scene(graph, positions, width, height, this.yaw, this.pitch, this.zoom);
      const styles = getComputedStyle(this.canvas), fg = styles.color, accent = styles.getPropertyValue('--accent').trim() || '#79b8ff';
      ctx.font = '12px system-ui'; ctx.lineWidth = 1;
      for (const axis of this.geometry.axes) {
        ctx.globalAlpha = .35; ctx.strokeStyle = fg; ctx.fillStyle = fg;
        ctx.beginPath(); ctx.moveTo(axis.from.x, axis.from.y); ctx.lineTo(axis.to.x, axis.to.y); ctx.stroke(); ctx.fillText(axis.name, axis.to.x + 5, axis.to.y);
      }
      for (const edge of this.geometry.edges) {
        if (!edge.from || !edge.to) continue;
        const a = edge.from, b = edge.to;
        ctx.globalAlpha = .75; ctx.strokeStyle = accent; ctx.fillStyle = accent;
        ctx.setLineDash(edge.kind === 'next' ? [5, 4] : []);
        const length = Math.hypot(b.x - a.x, b.y - a.y) || 1, bend = edge.kind === 'next' ? 65 : 25;
        const cx = (a.x + b.x) / 2 - (b.y - a.y) / length * bend, cy = (a.y + b.y) / 2 + (b.x - a.x) / length * bend;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.quadraticCurveTo(cx, cy, b.x, b.y); ctx.stroke(); ctx.setLineDash([]);
        const t = .7, x = (1-t)**2*a.x + 2*(1-t)*t*cx + t*t*b.x, y = (1-t)**2*a.y + 2*(1-t)*t*cy + t*t*b.y;
        const angle = Math.atan2((1-t)*(cy-a.y)+t*(b.y-cy), (1-t)*(cx-a.x)+t*(b.x-cx));
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - 8 * Math.cos(angle - .4), y - 8 * Math.sin(angle - .4)); ctx.lineTo(x - 8 * Math.cos(angle + .4), y - 8 * Math.sin(angle + .4)); ctx.closePath(); ctx.fill();
        ctx.fillText(edge.label, .25*a.x + .5*cx + .25*b.x + 4, .25*a.y + .5*cy + .25*b.y - 6);
      }
      ctx.globalAlpha = 1;
      for (const item of this.geometry.items) {
        const active = item.key === selected, w = Math.max(85, Math.min(200, ctx.measureText(item.name).width + 30));
        item.box = { x: item.x - w / 2, y: item.y - 23, w, h: 46 };
        ctx.fillStyle = styles.getPropertyValue('--node-bg').trim() || '#252932'; ctx.strokeStyle = active ? accent : fg; ctx.lineWidth = active ? 2 : .6;
        ctx.fillRect(item.box.x, item.box.y, w, 46); ctx.strokeRect(item.box.x, item.box.y, w, 46);
        ctx.fillStyle = fg; ctx.textAlign = 'center'; ctx.fillText(item.name, item.x, item.y - 2, w - 12);
        ctx.globalAlpha = .65; ctx.font = '10px system-ui'; ctx.fillText(item.kind === 'instance' ? item.target : item.kind, item.x, item.y + 13, w - 12);
        ctx.font = '12px system-ui'; ctx.globalAlpha = 1; ctx.textAlign = 'left';
      }
    }
    pick(x, y) { return this.geometry?.items.slice().reverse().find(item => item.box && x >= item.box.x && x <= item.box.x + item.box.w && y >= item.box.y && y <= item.box.y + item.box.h); }
  }
  const exported = { scene, NativeGraphRenderer };
  if (typeof module !== 'undefined') module.exports = exported; else root.PixelNativeGraph = exported;
})(globalThis);
