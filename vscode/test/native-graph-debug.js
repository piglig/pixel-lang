const assert = require('node:assert/strict');
const { NativeGraphDebug } = require('../src/native-graph-debug');
function fixture() {
  const project = { root: '/project', entry: 'main.pxl', files: { 'main.pxl': 'flow A() {}', 'lib.pxl': 'fn B() {}' } };
  const snapshot = { project, projection: { graphs: [{ source: 'main.pxl', node: 10 }] } };
  const frames = [{ frame: 1, source: 'main.pxl', declaration: 10, name: 'A' }, { frame: 3, source: 'main.pxl', declaration: 10, name: 'A' }];
  const messages = [], calls = [];
  let collected = project;
  const session = { snapshot, collect: async () => collected };
  const debug = { type: 'pixellang', async customRequest(method, args) {
    calls.push({ method, args });
    if (method === 'pixelFlowFrames') return { revision: 7, z: 20, project, frames };
    if (method === 'pixelFlowGraph') return { revision: 7, z: 20, source: 'main.pxl', declaration: 10, frame: args.frame, start: args.start, nodes: [] };
    if (method === 'pixelFlowSpace') return { time: 20, entities: [], total: 0 };
    return { variables: [{ name: 'x', value: '1' }] };
  } };
  let active = debug;
  const overlay = new NativeGraphDebug(session, () => active, message => messages.push(message));
  return { overlay, session, debug, messages, calls, project, setActive(v) { active = v; }, changeSources() { collected = { ...project, files: { ...project.files, 'lib.pxl': 'changed' } }; } };
}
(async () => {
  const f = fixture(); await f.overlay.refresh(3, 50);
  assert.equal(f.messages.at(-1).selected, 3);
  assert.equal(f.calls.at(-1).args.start, 50);
  await f.overlay.children(['flow', 3, 'oldState']);
  assert.equal(f.messages.at(-1).type, 'runtime-children');
  await f.overlay.spatial(['flow', 3, 'oldState', 'world'], 0, { origin: 0, radius: 2 });
  assert.equal(f.messages.at(-1).type, 'runtime-space');
  assert.equal(f.calls.at(-1).method, 'pixelFlowSpace');
  f.overlay.clear(); await f.overlay.children(['flow', 3]);
  assert.equal(f.messages.at(-1).type, 'runtime');
  const mismatch = fixture(); mismatch.changeSources(); await mismatch.overlay.refresh();
  assert.match(mismatch.messages.at(-1).message, /Source differs/);
  assert.equal(mismatch.calls.length, 1);
  for (const invalidate of [f => f.overlay.clear(), f => { f.session.snapshot = undefined; }, f => f.setActive(undefined)]) {
    const raced = fixture(); let finish;
    const original = raced.debug.customRequest;
    raced.debug.customRequest = (method, args) => method === 'pixelFlowGraph' ? new Promise(resolve => { finish = resolve; }) : original(method, args);
    const work = raced.overlay.refresh(); await new Promise(resolve => setImmediate(resolve));
    invalidate(raced);
    finish({ revision: 7, z: 20, source: 'main.pxl', declaration: 10 }); await work;
    assert(!raced.messages.some(message => message.view));
  }
  const stale = fixture(), original = stale.debug.customRequest;
  stale.debug.customRequest = async (method, args) => { const value = await original(method, args); return method === 'pixelFlowGraph' ? { ...value, z: 21 } : value; };
  await stale.overlay.refresh(); assert.match(stale.messages.at(-1).message, /expired/);
  for (const method of ['spatial', 'children']) {
    for (const failed of [false, true]) {
      const raced = fixture(); await raced.overlay.refresh();
      const current = raced.overlay.current, original = raced.debug.customRequest;
      let resolve, reject;
      raced.debug.customRequest = () => new Promise((yes, no) => { resolve = yes; reject = no; });
      const work = method === 'spatial' ? raced.overlay.spatial(['flow', 1], 0, undefined, 42) : raced.overlay.children(['flow', 1], 0, 42);
      raced.overlay.cancelChildren(); const count = raced.messages.length;
      if (failed) reject(Error('Late error')); else resolve({ variables: [] });
      await work;
      assert.equal(raced.messages.length, count, 'Cancelled inspection must not navigate or report an error');
      assert.equal(raced.overlay.current, current, 'Returning to flow retains the paused graph');
      raced.debug.customRequest = original;
      if (method === 'spatial') await raced.overlay.spatial(['flow', 1], 0, undefined, 43);
      else await raced.overlay.children(['flow', 1], 0, 43);
      assert.equal(raced.messages.at(-1).requestId, 43);
    }
  }
  console.log('Native graph debug bridge: source/time fences, nested frame selection, paging and late replies passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
