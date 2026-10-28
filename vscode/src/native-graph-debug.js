// A graph overlay is valid only for the exact source snapshot and paused DAP revision.
const stamp = project => JSON.stringify([project.root, project.entry, project.lock_error || '', Object.entries(project.files).sort(([a], [b]) => a.localeCompare(b))]);
class NativeGraphDebug {
  constructor(session, active, publish) { Object.assign(this, { session, active, publish, serial: 0 }); }
  cancelChildren() { this.childSerial = (this.childSerial || 0) + 1; }
  clear(message = 'Pause a source debug session to inspect values.') {
    this.serial++; this.cancelChildren(); this.current = undefined;
    this.publish({ type: 'runtime', message });
  }
  async refresh(frame, start = 0) {
    this.clear('Reading paused state…');
    const serial = this.serial, snapshot = this.session.snapshot, debug = this.active();
    if (!snapshot || debug?.type !== 'pixellang') { this.clear(); return; }
    const valid = () => serial === this.serial && this.session.snapshot === snapshot && this.active() === debug;
    try {
      const status = await debug.customRequest('pixelFlowFrames');
      if (!valid()) return;
      if (stamp(status.project) !== stamp(snapshot.project) || stamp(await this.session.collect(this.session.uri)) !== stamp(snapshot.project)) {
        if (valid()) this.clear('Source differs from the debug session. Restart debugging to inspect this graph.');
        return;
      }
      if (!valid()) return;
      const frames = status.frames.filter(item => snapshot.projection.graphs.some(graph => graph.source === item.source && graph.node === item.declaration));
      const selected = frames.find(item => item.frame === frame) || frames[0];
      if (!selected) { this.clear('No native flow is active at this pause.'); return; }
      const args = { revision: status.revision, frame: selected.frame, start, count: 50 };
      const view = await debug.customRequest('pixelFlowGraph', args);
      if (!valid()) return;
      if (view.revision !== status.revision || view.z !== status.z || view.source !== selected.source || view.declaration !== selected.declaration) throw Error('Graph debug snapshot expired.');
      this.current = { debug, snapshot, args, serial };
      this.publish({ type: 'runtime', frames, selected: selected.frame, view });
    } catch (error) { if (valid()) this.clear(error.message); }
  }
  async spatial(path, start = 0, neighbors, requestId) {
    const current = this.current, childSerial = ++this.childSerial;
    if (!current) return;
    try {
      const view = await current.debug.customRequest('pixelFlowSpace', { ...current.args, path, start, count: 50, neighbors });
      if (childSerial !== this.childSerial || current !== this.current || this.active() !== current.debug || this.session.snapshot !== current.snapshot) return;
      this.publish({ type: 'runtime-space', view, neighbors, requestId });
    } catch (error) {
      if (childSerial === this.childSerial && current === this.current && this.active() === current.debug && this.session.snapshot === current.snapshot)
        this.publish({ type: 'runtime-inspection-error', requestId, message: error.message });
    }
  }
  async children(path, start = 0, requestId) {
    const current = this.current, childSerial = ++this.childSerial;
    if (!current) return;
    try {
      const values = await current.debug.customRequest('pixelFlowChildren', { ...current.args, path, start, count: 50 });
      if (childSerial !== this.childSerial || current !== this.current || this.active() !== current.debug || this.session.snapshot !== current.snapshot) return;
      this.publish({ type: 'runtime-children', path, start, variables: values.variables, requestId });
    } catch (error) {
      if (childSerial === this.childSerial && current === this.current && this.active() === current.debug && this.session.snapshot === current.snapshot)
        this.publish({ type: 'runtime-inspection-error', requestId, message: error.message });
    }
  }
}
module.exports = { NativeGraphDebug, stamp };
