const assert = require('node:assert/strict');
const {mock} = require('node:test');
const {EventEmitter} = require('node:events');
const {PassThrough, Writable} = require('node:stream');
const child = new EventEmitter(), sent = [], active = new Set();
child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.kill = () => {};
child.stdin = new Writable({write(chunk, encoding, done) {
  const request = JSON.parse(chunk); sent.push(request);
  if (request.operation !== 'cancel') { active.add(request.id); assert(active.size <= 4); }
  done();
}});
mock.method(require('node:child_process'), 'spawn', () => child);
const {Bridge} = require('../src/bridge');
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const logs = [];
  const bridge = new Bridge('python', '.', {append(){}, appendLine(message){logs.push(message);}});
  const controllers = Array.from({length:12}, () => new AbortController());
  const promises = controllers.map((controller, index) => bridge.call('workspace', {index}, {signal:controller.signal})
    .then(value => ({value}), error => ({error})));
  assert.equal(active.size, 4);
  controllers[8].abort();
  assert.equal(sent.filter(request => request.operation === 'cancel').length, 0);
  controllers[0].abort();
  assert.equal(sent.filter(request => request.operation === 'cancel').length, 1);
  assert.equal(bridge.active, 4);
  assert.equal(sent.filter(request => request.operation === 'studio').length, 4);
  function reply(id) {
    active.delete(id);
    child.stdout.write(JSON.stringify({id, result:{id}})+'\n');
  }
  reply(1); await tick();
  assert.equal(bridge.active, 4);
  while (bridge.pending.size) {
    for (const id of [...active]) reply(id);
    await tick();
  }
  const results = await Promise.all(promises);
  assert.equal(results.filter(result => result.error?.name === 'AbortError').length, 2);
  assert.equal(results.filter(result => result.value).length, 10);
  assert.equal(sent.filter(request => request.operation === 'studio').length, 11);
  assert.equal(bridge.active, 0);
  assert.equal(logs.length, 11);
  for (const message of logs)
    assert.match(message, /^Studio workspace: \d+ ms \(\d+ ms in transport queue\), ok$/);
  const outlines = Array.from({length: 6}, () => bridge.call('document-symbols'));
  assert.equal(active.size, 1, 'Only one editor request may occupy the server');
  const build = bridge.call('build');
  assert.equal(active.size, 2, 'Build bypasses queued background queries');
  assert.equal(sent.at(-1).method, 'build');
  reply(sent.at(-1).id); await tick();
  await build;
  while (bridge.pending.size) {
    for (const id of [...active]) reply(id);
    await tick();
  }
  await Promise.all(outlines);
  bridge.dispose(); mock.restoreAll();
  console.log('Bridge bounds concurrent dispatch, drops cancelled queued work and retains active cancellation slots until acknowledged.');
})().catch(error => {console.error(error);process.exitCode=1;});
