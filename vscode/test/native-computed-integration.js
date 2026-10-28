const vscode = require('vscode'), assert = require('node:assert/strict'), path = require('node:path');
const source = `// 空间 🛰️
frame World = meters
fn Count(world: Space[World, string, bool]) -> int {
 return world.size()
}
flow Inspect() step(world: Space[World, string, bool]) -> int {
 let count = Count(world)
 output count
}
effect fn main() {
 let base = Point3::[World]{x: 0.0, y: 0.0, z: 1.0}
 let offset = Vec3::[World]{x: 0.0, y: 0.0, z: 2.0}
 let moved = base + offset
 let transform = Transform3::[World, World]{rotation: [[1.0,0.0,0.0],[0.0,1.0,0.0],[0.0,0.0,1.0]], translation: Vec3::[World]{x:0.0,y:0.0,z:4.0}}
 let target = transform.point(moved)
 let world = Space::[World, string, bool]("room").insert(target, "moving")
 print(Inspect().step(world))
}
`;
async function waitFor(test, label) {
 const until = Date.now() + 90000;
 while (Date.now() < until) {
  if (test()) return;
  await new Promise(resolve => setTimeout(resolve, 40));
 }
 throw Error('Timed out: ' + label);
}
async function run() {
 const api = await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
 await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
 const uri = vscode.Uri.file(path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, 'main.pxl'));
 const document = await vscode.workspace.openTextDocument(uri);
 const editor = await vscode.window.showTextDocument(document);
 assert(await editor.edit(builder => builder.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), source)));
 await document.save();
 const panel = await api.nativeGraphs.open(uri);
 const stops = [];
 const tracker = vscode.debug.registerDebugAdapterTrackerFactory('pixellang', {
  createDebugAdapterTracker(session) { return {onDidSendMessage(message) {
   if (message.type === 'event' && message.event === 'stopped') stops.push(session.id);
  }}; }
 });
 let debug;
 async function stop() {
  await waitFor(() => stops.includes(debug.id), 'debug pause');
  stops.splice(stops.indexOf(debug.id), 1);
 }
 try {
  await waitFor(() => panel.snapshot, 'computed coordinate projection');
  assert.deepEqual(panel.snapshot.projection.diagnostics, []);
  const expression = 'transform.point(moved)';
  const projected = panel.snapshot.projection.coordinates.find(item =>
   [...source].slice(item.range.start, item.range.end).join('') === expression);
  assert(projected);
  assert.deepEqual(projected.axes, []);
  assert(await vscode.debug.startDebugging(undefined, {type:'pixellang',request:'launch',name:'Computed spatial position',program:uri.fsPath,stopOnEntry:true}));
  debug = vscode.debug.activeDebugSession;
  await stop();
  const breaks = await debug.customRequest('setBreakpoints', {source:{path:uri.fsPath},breakpoints:[{line:4}]});
  assert(breaks.breakpoints[0].verified);
  await debug.customRequest('continue', {threadId:1});
  await stop();
  const status = await debug.customRequest('pixelFlowFrames');
  const args = {revision:status.revision,frame:status.frames[0].frame};
  const graph = await debug.customRequest('pixelFlowGraph', args);
  const input = graph.inputs.find(item => item.value.name === 'world');
  const view = await debug.customRequest('pixelFlowSpace', {...args,path:input.value.path});
  assert.deepEqual(view.entities[0].position, [0,0,7]);
  assert.deepEqual(view.entities[0].origin, {source:'main.pxl',node:projected.node});
  const message = {epoch:panel.snapshot.epoch,source:projected.source,node:projected.node};
  await panel.revealCoordinate(message);
  assert.equal(document.offsetAt(editor.selection.active), source.indexOf(expression));
  await assert.rejects(panel.coordinateEdit({...message,values:{z:8}}), /edited as expressions in source/);
  assert.equal(document.getText(), source);
  assert.equal(document.isDirty, false);
  console.log('PASS: computed entity position, DAP origin, Unicode source navigation and rejected literal overwrite in installed Studio.');
 } finally {
  if (debug) await Promise.race([vscode.debug.stopDebugging(debug),new Promise(resolve=>setTimeout(resolve,3000))]);
  tracker.dispose(); panel.dispose();
 }
}
module.exports = {run};
