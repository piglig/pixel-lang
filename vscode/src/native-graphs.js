const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { applyGraphEdit, planEdit, validateGraphEdit } = require('./native-graph-edits');
const { planCoordinateEdit } = require('./native-coordinate-edits');
const { NativeGraphDebug, stamp } = require('./native-graph-debug');

class NativeGraphSession {
  constructor(vscode, collect, service, uri, publish, resolveUri = filename => vscode.Uri.file(filename)) {
    Object.assign(this, { vscode, collect, service, uri, publish, resolveUri, epoch: 0, closed: false });
  }
  invalidate() {
    this.epoch++;
    this.debug?.clear();
    this.pending?.abort();
    this.snapshot = undefined;
  }
  async refresh() {
    if (this.closed) return;
    this.invalidate();
    const epoch = this.epoch, controller = new AbortController();
    this.pending = controller;
    this.publish({ type: 'pending' });
    try {
      const project = await this.collect(this.uri);
      if (project.lock_error) throw Error(project.lock_error);
      if (epoch !== this.epoch || this.closed) return;
      const result = await this.service().call('native-graphs', { files: project.files, entry: project.entry }, { signal: controller.signal });
      if (epoch !== this.epoch || this.closed) return;
      if (stamp(await this.collect(this.uri)) !== stamp(project)) throw Error('Sources changed during analysis; refresh the graph.');
      if (epoch !== this.epoch || this.closed) return;
      if (result.projection?.version !== 'pixellang-native-graphs/2') throw Error('The runtime does not provide native graph projections.');
      this.snapshot = { project, projection: result.projection, epoch };
      const coordinateText = {}, coordinateSources = new Map();
      for (const item of result.projection.coordinates || []) {
        if (!coordinateSources.has(item.source)) {
          const points = [...project.files[item.source]], lines = [0];
          for (let i = 0; i < points.length; i++) if (points[i] === '\n') lines.push(i + 1);
          coordinateSources.set(item.source, { points, lines });
        }
        const { points, lines } = coordinateSources.get(item.source);
        let low = 0, high = lines.length;
        while (low < high) { const middle = (low + high) >>> 1; if (lines[middle] <= item.range.start) low = middle + 1; else high = middle; }
        coordinateText[JSON.stringify([item.source, item.node])] = {
          line: low,
          axes: Object.fromEntries(item.axes.map(axis => [axis.name, points.slice(axis.range.start, axis.range.end).join('')])),
        };
      }
      const initializerText = {}, expressionText = {};
      for (const graph of result.projection.graphs) {
        const points = [...project.files[graph.source]];
        for (const item of [...graph.values, ...graph.children, ...graph.states, ...graph.updates, ...graph.outputs]) {
          const span = item.expression?.range || item.constructor?.range || item.initializer;
          expressionText[JSON.stringify([graph.source, item.node])] = points.slice(span.start, span.end).join('');
        }
        for (const node of graph.children) initializerText[JSON.stringify([graph.source, node.node])] =
          (node.initializers || []).map(span => points.slice(span.start, span.end).join(''));
      }
      this.publish({ type: 'state', epoch, projection: result.projection, coordinateText, initializerText, expressionText });
      void this.debug?.refresh();
      return this.snapshot;
    } catch (error) {
      if (epoch === this.epoch && !this.closed && error.name !== 'AbortError') this.publish({ type: 'error', message: error.message });
    }
  }
  selection(epoch, source, node) {
    const snapshot = this.snapshot;
    if (this.closed || !snapshot || snapshot.epoch !== epoch) throw Error('Graph snapshot expired; refresh before editing.');
    const graph = snapshot.projection.graphs.find(item => item.source === source && item.node === node);
    if (!graph || !Object.hasOwn(snapshot.project.files, source)) throw Error('Unknown source graph.');
    return { snapshot, graph };
  }
  async document(snapshot, graph) {
    const filename = snapshot.project.paths?.[graph.source] || path.resolve(snapshot.project.root, graph.source);
    return this.vscode.workspace.openTextDocument(this.resolveUri(filename));
  }
  async edit(message) {
    if (this.editing) throw Error('A graph edit is already in progress.');
    this.editing = true;
    try {
      const { snapshot, graph } = this.selection(message.epoch, message.source, message.graph);
      if (graph.source.startsWith('deps/')) throw Error('Edit dependency sources in their own project.');
      const original = snapshot.project.files[graph.source];
      let candidate = original;
      const edits = planEdit(candidate, graph, message.action);
      for (const edit of edits) candidate = candidate.slice(0, edit.start) + edit.text + candidate.slice(edit.end);
      const result = await this.service().call('native-graphs', {
        files: { ...snapshot.project.files, [graph.source]: candidate }, entry: snapshot.project.entry,
      });
      this.selection(message.epoch, message.source, message.graph);
      const updated = result.projection?.graphs.find(item => item.source === graph.source && item.range.start === graph.range.start && item.name === graph.name);
      validateGraphEdit(graph, updated, message.action, original, candidate);
      if (['add-instance', 'set-initializers'].includes(message.action.kind)) {
        const childName = message.action.kind === 'add-instance' ? message.action.name : graph.children.find(item => item.node === message.action.node)?.name;
        const child = updated.children.find(item => item.name === childName);
        const span = child.constructor.range;
        const diagnostic = result.projection.diagnostics.find(item => item.source === graph.source && item.start >= span.start && item.end <= span.end);
        if (diagnostic) throw Error(diagnostic.message);
      }
      // Incomplete bindings and types remain editable. Invalid syntax cannot
      // escape an expression field or turn it into additional declarations.
      const syntaxError = result.projection.diagnostics.find(item => ['lex', 'parse'].includes(item.phase));
      if (syntaxError) throw Error(syntaxError.message);
      const document = await this.document(snapshot, graph);
      const version = document.version;
      const editor = await this.vscode.window.showTextDocument(document, { viewColumn: this.vscode.ViewColumn.One, preserveFocus: true });
      if (stamp(await this.collect(this.uri)) !== stamp(snapshot.project)) throw Error('Project sources changed; refresh before editing.');
      this.selection(message.epoch, message.source, message.graph);
      await applyGraphEdit(this.vscode, editor, { graph, uri: document.uri.toString(), version, text: snapshot.project.files[graph.source] }, message.action);
      await this.refresh();
    } finally { this.editing = false; }
  }
  async revealCoordinate(message) {
    const snapshot = this.snapshot;
    if (!snapshot || this.closed || snapshot.epoch !== message.epoch) throw Error('Coordinate snapshot expired; refresh first.');
    const coordinates = (snapshot.projection.coordinates || []).find(item => item.source === message.source && item.node === message.node);
    if (!coordinates) throw Error('Unknown native coordinate expression.');
    const document = await this.document(snapshot, coordinates);
    if (this.snapshot !== snapshot || document.getText() !== snapshot.project.files[coordinates.source]) throw Error('Source changed; refresh first.');
    const offset = [...document.getText()].slice(0, coordinates.range.start).join('').length;
    const position = document.positionAt(offset);
    const editor = await this.vscode.window.showTextDocument(document, { viewColumn: this.vscode.ViewColumn.One, preserveFocus: true });
    editor.selection = new this.vscode.Selection(position, position);
    editor.revealRange(new this.vscode.Range(position, position));
  }
  async coordinateEdit(message) {
    if (this.editing) throw Error('A source edit is already in progress.');
    this.editing = true;
    try {
      const snapshot = this.snapshot;
      if (!snapshot || this.closed || snapshot.epoch !== message.epoch) throw Error('Coordinate snapshot expired; refresh before editing.');
      const coordinates = (snapshot.projection.coordinates || []).find(item => item.source === message.source && item.node === message.node);
      if (!coordinates || coordinates.source.startsWith('deps/')) throw Error('Choose a coordinate in editable project source.');
      const document = await this.document(snapshot, coordinates), version = document.version;
      const text = snapshot.project.files[coordinates.source];
      const edits = planCoordinateEdit(text, coordinates, message.values);
      const editor = await this.vscode.window.showTextDocument(document, { viewColumn: this.vscode.ViewColumn.One, preserveFocus: true });
      if (stamp(await this.collect(this.uri)) !== stamp(snapshot.project) || this.snapshot !== snapshot || this.closed || document.version !== version || document.getText() !== text) throw Error('Project sources changed; refresh before editing.');
      const applied = await editor.edit(builder => {
        for (const edit of edits) builder.replace(new this.vscode.Range(document.positionAt(edit.start), document.positionAt(edit.end)), edit.text);
      }, { undoStopBefore: true, undoStopAfter: true });
      if (!applied) throw Error('Source changed before the coordinate edit could be applied.');
      await this.refresh();
    } finally { this.editing = false; }
  }
  async reveal(message) {
    const { snapshot, graph } = this.selection(message.epoch, message.source, message.graph);
    const document = await this.document(snapshot, graph);
    if (document.getText() !== snapshot.project.files[graph.source]) throw Error('Source changed; refresh the graph.');
    const selected = [...graph.values, ...graph.children, ...graph.states, ...graph.updates, ...graph.outputs, ...graph.inputs, ...graph.initialInputs].find(item => item.node === message.node) || graph;
    const offset = [...document.getText()].slice(0, selected.range.start).join('').length;
    const position = document.positionAt(offset);
    const editor = await this.vscode.window.showTextDocument(document, { viewColumn: this.vscode.ViewColumn.One, preserveFocus: true });
    editor.selection = new this.vscode.Selection(position, position);
    editor.revealRange(new this.vscode.Range(position, position));
  }
  dispose() { this.closed = true; this.invalidate(); }
}
function registerNativeGraphs(vscode, context, service, collect, resolveUri) {
  const overlays = new Set();
  context.subscriptions.push(vscode.debug.registerDebugAdapterTrackerFactory('pixellang', {
    createDebugAdapterTracker(debug) { return { onDidSendMessage(message) {
      if (debug !== vscode.debug.activeDebugSession || message.type !== 'event') return;
      for (const overlay of overlays) {
        if (message.event === 'stopped') void overlay.refresh();
        else if (['pixelGraphInvalidated', 'continued', 'terminated', 'exited'].includes(message.event)) overlay.clear();
      }
    } }; }
  }), vscode.debug.onDidChangeActiveDebugSession(() => {
    for (const overlay of overlays) void overlay.refresh();
  }));
  async function open(uri) {
    uri ||= vscode.window.activeTextEditor?.document.uri;
    if (!uri || path.extname(uri.fsPath) !== '.pxl') throw Error('Open a PixelLang source file first.');
    const panel = vscode.window.createWebviewPanel('pixellang.nativeGraph', 'PixelLang · Native Graph', vscode.ViewColumn.Beside,
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, 'media')] });
    let timer;
    const session = new NativeGraphSession(vscode, collect, service, uri, message => panel.webview.postMessage(message), resolveUri);
    session.debug = new NativeGraphDebug(session, () => vscode.debug.activeDebugSession, message => panel.webview.postMessage(message));
    overlays.add(session.debug);
    const layoutKey = 'native-graph-layout:' + uri.toString();
    let layouts = context.workspaceState.get(layoutKey, {});
    const nonce = crypto.randomBytes(16).toString('hex');
    let html = fs.readFileSync(path.join(context.extensionPath, 'media/native-graph.html'), 'utf8')
      .replaceAll('{{nonce}}', nonce).replaceAll('{{csp}}', panel.webview.cspSource);
    for (const [key, file] of Object.entries({ css: 'native-graph.css', renderer: 'native-graph-renderer.js', spaceRenderer: 'native-space-renderer.js', script: 'native-graph.js' })) {
      html = html.replace('{{' + key + '}}', panel.webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, 'media', file)).toString());
    }
    const messages = panel.webview.onDidReceiveMessage(async message => {
      try {
        if (message.type === 'ready') {
          await panel.webview.postMessage({ type: 'layouts', layouts });
          await session.refresh();
        } else if (message.type === 'refresh') await session.refresh();
        else if (message.type === 'coordinate-source') await session.revealCoordinate(message);
        else if (message.type === 'coordinate-edit') await session.coordinateEdit(message);
        else if (message.type === 'edit') await session.edit(message);
        else if (message.type === 'runtime-select') await session.debug.refresh(message.frame, message.start);
        else if (message.type === 'runtime-cancel') session.debug.cancelChildren();
        else if (message.type === 'runtime-space') await session.debug.spatial(message.path, message.start, message.neighbors, message.requestId);
        else if (message.type === 'runtime-expand') await session.debug.children(message.path, message.start, message.requestId);
        else if (message.type === 'source') await session.reveal(message);
        else if (message.type === 'layout') {
          const { graph } = session.selection(message.epoch, message.source, message.graph);
          if (![...graph.values, ...graph.children].some(node => node.name === message.name)) throw Error('Unknown layout node.');
          if (!Array.isArray(message.position) || message.position.length !== 3 || !message.position.every(n => Number.isFinite(n) && Math.abs(n) <= 100)) throw Error('Layout coordinates must be between -100 and 100.');
          const key = JSON.stringify([graph.source, graph.name]);
          layouts = { ...layouts, [key]: { ...layouts[key], [message.name]: message.position } };
          await context.workspaceState.update(layoutKey, layouts);
          await panel.webview.postMessage({ type: 'layouts', layouts });
        }
      } catch (error) { if (!session.closed) await panel.webview.postMessage({ type: 'error', message: error.message, retain: Boolean(session.snapshot) }); }
    });
    const invalidate = () => {
      session.invalidate();
      panel.webview.postMessage({ type: 'pending' });
      clearTimeout(timer);
      timer = setTimeout(() => session.refresh(), 250);
    };
    const changed = vscode.workspace.onDidChangeTextDocument(event => {
      if (event.document.languageId !== 'pixellang' && !/pixel\.(toml|lock)$/.test(event.document.uri.fsPath)) return;
      invalidate();
    });
    const watcher = vscode.workspace.createFileSystemWatcher('**/{*.pxl,pixel.toml,pixel.lock}');
    const fileEvents = [watcher.onDidChange(invalidate), watcher.onDidCreate(invalidate), watcher.onDidDelete(invalidate)];
    panel.onDidDispose(() => { clearTimeout(timer); session.dispose(); overlays.delete(session.debug); changed.dispose(); watcher.dispose(); fileEvents.forEach(event => event.dispose()); messages.dispose(); });
    panel.webview.html = html;
    context.subscriptions.push(panel);
    return session;
  }
  context.subscriptions.push(vscode.commands.registerCommand('pixellang.openNativeGraph', async uri => {
    try { return await open(uri); } catch (error) { vscode.window.showErrorMessage(error.message); }
  }));
  return { open };
}
module.exports = { NativeGraphSession, registerNativeGraphs };
