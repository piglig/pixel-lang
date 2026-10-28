// Integration harness: projections in this test come from the native compiler,
// not fabricated ranges or a JavaScript parser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { planEdit } = require('../src/native-graph-edits');
const { NativeGraphSession } = require('../src/native-graphs');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const graph = data.projection.graphs.find(item => item.name === 'Parent');
const action = { ...data.action };
if (['set-initializers', 'set-expression'].includes(action.kind)) action.node = graph.children[0].node;
let candidate = data.text;
for (const edit of planEdit(candidate, graph, action)) candidate = candidate.slice(0, edit.start) + edit.text + candidate.slice(edit.end);
if (!data.checked) process.stdout.write(JSON.stringify({ candidate }));
else (async () => {
  let content = data.text, writes = 0;
  const uri = { toString: () => 'file:///project/main.pxl' };
  const document = { uri, version: 1, getText: () => content, positionAt: offset => offset };
  const editor = { document, edit: async (callback, options) => {
    assert.deepEqual(options, { undoStopBefore: true, undoStopAfter: true });
    callback({ replace: (range, value) => { content = content.slice(0, range.start) + value + content.slice(range.end); } });
    writes++; document.version++; return true;
  } };
  const vscode = { Uri: { file: () => uri }, ViewColumn: { One: 1 },
    Range: class { constructor(start, end) { Object.assign(this, { start, end }); } },
    workspace: { openTextDocument: async () => document }, window: { showTextDocument: async () => editor } };
  const collect = async () => ({ root: '/project', entry: 'main.pxl', files: { 'main.pxl': content } });
  const messages = [];
  const service = { call: async (method, args) => {
    assert.equal(method, 'native-graphs');
    return { projection: args.files['main.pxl'] === data.text ? data.projection : data.checked };
  } };
  const session = new NativeGraphSession(vscode, collect, () => service, uri, message => messages.push(message));
  await session.refresh();
  const values = messages.at(-1).initializerText[JSON.stringify(['main.pxl', graph.children[0].node])];
  assert.deepEqual(values, (graph.children[0].initializers || []).map(span => [...data.text].slice(span.start, span.end).join('')));
  const edit = () => session.edit({ epoch: session.epoch, source: 'main.pxl', graph: graph.node, action });
  if (data.reject) { await assert.rejects(edit); assert.equal(writes, 0); assert.equal(content, data.text); }
  else { await edit(); assert.equal(content, candidate); assert.equal(writes, 1); }
  process.stdout.write(JSON.stringify({ ok: true }));
})().catch(error => { console.error(error); process.exitCode = 1; });
