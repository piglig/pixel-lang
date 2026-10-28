const vscode = require('vscode'), assert = require('node:assert/strict'), path = require('node:path');
const source = `frame World = meters
fn Count(world: Space[World, string, Cost]) -> int {
 let observer = world.get(0)
 return len(world.neighbors(observer, 2.0))
}
flow Inspect() step(world: Space[World, string, Cost]) -> int {
 let count = Count(world)
 output count
}
record Cost { weights: [int] }
effect fn main() {
 let origin = Point3::[World]{x: 0.0, y: 0.0, z: 0.0}
 let near = Point3::[World]{x: 0.0, y: 0.0, z: 2.0}
 let empty = Space::[World, string, Cost]("room")
 let first = empty.insert(origin, "observer")
 let populated = first.insert(near, "moving")
 let world = populated.connect(populated.get(0), populated.get(1), Cost{weights:[5,8]})
 print(Inspect().step(world))
}
`;
async function waitFor(test, label, milliseconds = 90000) {
 const deadline = Date.now() + milliseconds;
 while (Date.now() < deadline) { if (test()) return; await new Promise(resolve => setTimeout(resolve, 40)); }
 throw Error('Timed out: ' + label);
}
async function run() {
 const api = await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
 await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
 const uri = vscode.Uri.file(path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, 'main.pxl'));
 const document = await vscode.workspace.openTextDocument(uri);
 const editor = await vscode.window.showTextDocument(document);
 assert(await editor.edit(builder => builder.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), source)));
 const panel = await api.nativeGraphs.open(uri);
 await waitFor(() => panel.snapshot, 'native projection');
 assert.deepEqual(panel.snapshot.projection.diagnostics, []);
 const stops = [], outputs = [];
 const tracker = vscode.debug.registerDebugAdapterTrackerFactory('pixellang', {
  createDebugAdapterTracker(session) { return { onDidSendMessage(message) {
   if (message.type !== 'event') return;
   if (message.event === 'stopped') stops.push({ id: session.id, body: message.body });
   if (message.event === 'output') outputs.push(message.body.output);
  } }; }
 });
 let debug;
 async function stop() {
  await waitFor(() => stops.some(event => event.id === debug.id), 'debug pause');
  return stops.splice(stops.findIndex(event => event.id === debug.id), 1)[0];
 }
 async function launch() {
  assert(await vscode.debug.startDebugging(undefined, { type: 'pixellang', request: 'launch', name: 'Native spatial acceptance', program: uri.fsPath, stopOnEntry: true }));
  debug = vscode.debug.activeDebugSession;
  assert(debug); await stop();
  const breakpoints = await debug.customRequest('setBreakpoints', { source: {path:uri.fsPath}, breakpoints:[{line:4}] });
  assert(breakpoints.breakpoints[0].verified);
  await debug.customRequest('continue', {threadId:1}); await stop();
  const status = await debug.customRequest('pixelFlowFrames');
  assert.equal(status.frames.length, 1);
  const frame = status.frames[0];
  const args = { revision:status.revision, frame:frame.frame };
  const graph = await debug.customRequest('pixelFlowGraph',args);
  assert.equal(graph.source,'main.pxl');
  const input = graph.inputs.find(input => input.value.name === 'world'); assert(input?.value.path);
  const view = await debug.customRequest('pixelFlowSpace', {...args,path:input.value.path});
  return {status,args,input,view};
 }
 try {
  console.log('Native spatial debug: projection ready, launching DAP.');
  const first = await launch();
  assert.equal(first.view.entities[1].position[2],2);
  assert.equal(first.view.linkTotal,1);
  assert.deepEqual(first.view.links.map(link=>[link.id,link.source,link.target]),[[0,0,1]]);
  const cost = await debug.customRequest('pixelFlowChildren',{...first.args,path:first.view.links[0].payload.path,start:0,count:50});
  const weights = cost.variables.find(item=>item.name==='weights'); assert(weights?.path);
  const weightPage = await debug.customRequest('pixelFlowChildren',{...first.args,path:weights.path,start:1,count:1});
  assert.deepEqual(weightPage.variables.map(item=>item.value),['8']);
  const origin = first.view.entities[1].origin; assert(origin);
  // Debug launch saves the document, which invalidates and refreshes the graph.
  await waitFor(() => panel.snapshot?.project.files['main.pxl'] === document.getText(), 'saved source projection');
  const coordinates = panel.snapshot.projection.coordinates.find(item => item.source === origin.source && item.node === origin.node);
  assert(coordinates);
  const near = await debug.customRequest('pixelFlowSpace', {...first.args,path:first.input.value.path,neighbors:{origin:0,radius:2}});
  assert.equal(near.total,1);
  await debug.customRequest('stepBack',{threadId:1}); await stop();
  await assert.rejects(debug.customRequest('pixelFlowSpace',{...first.args,path:first.input.value.path}), /expired/);
  const historical = await debug.customRequest('pixelFlowFrames');
  assert(historical.z < first.status.z);
  const oldGraph = await debug.customRequest('pixelFlowGraph',{revision:historical.revision,frame:historical.frames[0].frame});
  const oldInput = oldGraph.inputs.find(item=>item.value.name==='world');
  const oldView = await debug.customRequest('pixelFlowSpace',{revision:historical.revision,frame:historical.frames[0].frame,path:oldInput.value.path});
  assert.equal(oldView.entities[1].position[2],2);
  assert.deepEqual(oldView.entities[1].origin,origin);
  assert.deepEqual(oldView.links,first.view.links);
  await waitFor(() => panel.debug.current?.args.revision === historical.revision, 'native graph receives current historical pause', 15000);
  await panel.coordinateEdit({epoch:panel.snapshot.epoch,source:origin.source,node:origin.node,values:{z:3}});
  assert.equal(document.getText(),source.replace('z: 2.0','z: 3.0'));
  assert(document.isDirty);
  await panel.debug.refresh();
  assert.equal(panel.debug.current, undefined, 'edited source must reject old debug snapshot');
  await vscode.debug.stopDebugging(debug);
  await waitFor(() => !vscode.debug.activeDebugSession, 'first debug session terminates');
  console.log('Native spatial debug: source edited and old overlay rejected, restarting DAP.');
  const second = await launch();
  assert.equal(second.view.entities[1].position[2],3);
  assert.deepEqual(second.view.links.map(link=>[link.id,link.source,link.target]),[[0,0,1]]);
  const far = await debug.customRequest('pixelFlowSpace', {...second.args,path:second.input.value.path,neighbors:{origin:0,radius:2}});
  assert.equal(far.total,0);
  await debug.customRequest('setBreakpoints',{source:{path:uri.fsPath},breakpoints:[]});
  await debug.customRequest('continue',{threadId:1});
  await waitFor(() => outputs.some(text=>text.trim()==='0'), 'edited program output');
  console.log('PASS: real VS Code native compiler, typed link payload paging/history, DAP pause, entity provenance, source coordinate edit, stale overlay invalidation and restarted neighborhood result.');
 } finally { if (debug) { await Promise.race([vscode.debug.stopDebugging(debug), new Promise(resolve => setTimeout(resolve, 3000))]); } tracker.dispose(); panel.dispose(); }
}
module.exports = {run, source};
