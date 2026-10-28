const assert = require('node:assert/strict');
const { PixelDebug } = require('../src/debug');
const messages = [], calls = [];
let pendingGraph, pendingFrames;
class EventEmitter {
  constructor() { this.event = () => {}; }
  fire(message) { messages.push(message); }
  dispose() {}
}
const adapter = new PixelDebug({ EventEmitter }, {
  service: () => ({ call: async (method, args) => {
    calls.push({ method, args });
    if (method === 'flow-frames') return pendingFrames || {z:args.z, frames:[{frame:1,source:'main.pxl',declaration:42,name:'Counter'}]};
    if (method === 'flow-graph') return pendingGraph ? pendingGraph : { z: args.z, nodes: [] };
    return { variables: [{ name: 'oldState', value: 'Fields (1)', type: '', runtime_type: '',
      namedVariables: 1, path: [...args.path, 'oldState'] }] };
  }}), publish() {}, reveal() {},
});
let seq = 1;
async function request(command, args) {
  const id = seq++;
  await adapter.handleMessage({ seq: id, command, arguments: args });
  return messages.find(message => message.type === 'response' && message.request_seq === id);
}
(async () => {
  adapter.result = { session: 'native', cursor: 12, state: {}, frames: [{ names: ['value'] }],
    flow_scopes: [{ frame: 0, name: 'Counter', path: ['flow', 1] }] };
  const scopes = await request('scopes', { frameId: 0 });
  const flow = scopes.body.scopes.find(scope => scope.name === 'Flow: Counter');
  assert(flow);
  assert(flow.variablesReference >= 100000);
  const values = await request('variables', { variablesReference: flow.variablesReference, start: 2, count: 4 });
  assert.deepEqual(calls.at(-1), { method: 'variables', args: {
    session: 'native', z: 12, path: ['flow', 1], start: 2, count: 4,
  }});
  assert.equal(values.body.variables[0].name, 'oldState');
  const child = values.body.variables[0].variablesReference;
  await request('variables', { variablesReference: child });
  assert.deepEqual(calls.at(-1).args.path, ['flow', 1, 'oldState']);
  adapter.stopped('step');
  const stale = await request('variables', { variablesReference: child });
  assert.equal(stale.success, false);
  assert.match(stale.message, /expired/);
  const ordinary = await request('scopes', { frameId: 1 });
  assert(!ordinary.body.scopes.some(scope => scope.name.startsWith('Flow:')));
  adapter.project = { root: '/project', entry: 'main.pxl', files: { 'main.pxl': 'flow Counter() {}' } };
  adapter.result.frames.push({ function: 'counter' });
  adapter.result.inspection = { ir: null };
  adapter.authoringProject = structuredClone(adapter.project);
  adapter.project = { ...adapter.project, files: { ...adapter.project.files, 'std/text.pxl': 'standard library source' } };
  const snapshot = (await request('pixelFlowFrames', {})).body;
  assert.deepEqual(snapshot.project, adapter.authoringProject);
  assert(!Object.hasOwn(snapshot.project.files, 'std/text.pxl'));
  assert(Object.hasOwn(adapter.project.files, 'std/text.pxl'));
  assert.deepEqual(snapshot.frames, [{ frame: 1, source: 'main.pxl', declaration: 42, name: 'Counter' }]);
  const args = { frame: 1, revision: snapshot.revision };
  assert.equal((await request('pixelFlowGraph', args)).success, true);
  assert.equal((await request('pixelFlowSpace', { ...args, path: ['flow', 1, 'oldState', 'world'], neighbors: { origin: 0, radius: 2 } })).success, true);
  assert.equal(calls.at(-1).method, 'native-space-view');
  assert.deepEqual(calls.at(-1).args.neighbors, { origin: 0, radius: 2 });
  assert.equal((await request('pixelFlowSpace', { ...args, path: ['stack', 0] })).success, false);
  assert.equal((await request('pixelFlowChildren', { ...args, path: ['stack'] })).success, false);
  assert.equal((await request('pixelFlowGraph', { ...args, revision: args.revision - 1 })).success, false);
  assert.equal((await request('pixelFlowGraph', { ...args, frame: 99 })).success, false);
  let resolveGraph;
  pendingGraph = new Promise(resolve => { resolveGraph = resolve; });
  const delayed = request('pixelFlowGraph', args);
  adapter.invalidateGraph();
  adapter.result.cursor++;
  adapter.stopped('step');
  resolveGraph({ z: 12, nodes: [] });
  assert.equal((await delayed).success, false);
  for (const expire of [() => adapter.invalidateGraph(), () => {adapter.result.cursor++;},
      () => {adapter.result.session = 'replacement';}]) {
    adapter.stopped('step');
    let finish;
    pendingFrames = new Promise(resolve => {finish=resolve;});
    const z=adapter.result.cursor;
    const delayed=request('pixelFlowFrames',{});
    expire(); finish({z,frames:[]});
    assert.equal((await delayed).success,false,'Late frame metadata must not cross pause/session boundaries');
    pendingFrames=undefined;
  }
  adapter.invalidateGraph();
  assert.equal((await request('pixelFlowFrames', {})).success, false);
  console.log('Native DAP flow scopes, paging and expired references passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
