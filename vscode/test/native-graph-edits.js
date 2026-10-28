const assert = require('node:assert/strict');
const { planEdit, validateGraphEdit } = require('../src/native-graph-edits');
const { scene } = require('../media/native-graph-renderer');
const text = '// 星 🛰️\r\nflow F(x:int)->int {\r\n  state memory = 0\r\n  let a = x + 1 // keep\r\n  instance child = Counter(2)\r\n  next memory = a\r\n  output child.step(a) + memory\r\n}\r\n';
const points = [...text];
const span = fragment => { const start = [...text.slice(0, text.indexOf(fragment))].length; return { start, end: start + [...fragment].length }; };
const expression = (node, fragment) => ({ node, type: 'int', range: span(fragment) });
const graph = { node: 20, name: 'F', source: 'main.pxl', range: { start: span('flow').start, end: points.length - 2 }, body: { start: span('{').start, end: points.length - 2 }, initialInputs: [], inputs: [{ node: 1, name: 'x', type: 'int' }],
  values: [{ node: 3, name: 'a', range: span('let a = x + 1'), expression: expression(10, 'x + 1') }],
  children: [{ node: 4, name: 'child', target: 'Counter', range: span('instance child = Counter(2)'), constructor: expression(11, 'Counter(2)'), initializers: [span('2')], inputs: [], result: 'int' }],
  states: [{ node: 2, name: 'memory', range: span('state memory = 0'), initializer: span('0') }],
  updates: [{ node: 5, state: 'memory', range: span('next memory = a'), expression: { node: 12, type: 'int', range: { start: span('next memory = a').end - 1, end: span('next memory = a').end } } }],
  outputs: [{ node: 6, range: span('output child.step(a) + memory'), expression: expression(13, 'child.step(a) + memory') }],
  dependencies: [{ source: 1, target: 3, reference: 10 }, { source: 3, target: 5, reference: 12 }, { source: 4, target: 6, reference: 13 }],
  palette: [{ target: 'Counter', kind: 'flow', generic: false, initialInputs: [{ name: 'seed', type: 'int' }] }, { target: 'Add', kind: 'fn', generic: false, initialInputs: [] }] };
function apply(action) { let result = text; for (const edit of planEdit(text, graph, action)) result = result.slice(0, edit.start) + edit.text + result.slice(edit.end); return result; }
assert.equal(apply({ kind: 'set-output', expression: 'a * 2' }), text.replace('output child.step(a) + memory', 'output a * 2'));
assert.equal(apply({ kind: 'set-next', state: 'memory', expression: 'memory + x' }), text.replace('next memory = a', 'next memory = memory + x'));
assert.equal(apply({ kind: 'set-expression', node: 3, expression: 'x * 3' }), text.replace('let a = x + 1', 'let a = x * 3'));
assert.equal(apply({ kind: 'set-initializers', node: 4, initializers: ['7'] }), text.replace('Counter(2)', 'Counter(7)'));
assert(apply({ kind: 'add-instance', name: 'other', target: 'Counter', initializers: ['memory'] }).includes('  instance other = Counter(memory)\r\n}'));
assert(apply({ kind: 'add-value', name: '计算', expression: 'a + memory' }).includes('  let 计算 = a + memory\r\n}'));
assert(apply({ kind: 'add-state', name: 'old', initializer: '0' }).includes('  state old = 0\r\n}'));
assert.equal(apply({ kind: 'remove-next', node: 5 }), text.replace('  next memory = a\r\n', ''));
assert.throws(() => apply({ kind: 'remove-next', node: 999 }), /expired/);
const removed = apply({ kind: 'remove-value', node: 3 });
assert(!removed.includes('let a')); assert(removed.includes('// keep')); assert(removed.includes('next memory = a')); assert(removed.includes('child.step(a)'));
assert(apply({ kind: 'remove-instance', node: 4 }).includes('output child.step(a)'));
assert.throws(() => apply({ kind: 'add-value', name: 'memory', expression: '0' }), /already/);
assert.throws(() => apply({ kind: 'add-value', name: 'x; print(1)', expression: '0' }), /identifier/);
for (const name of ['with', 'inout'])
  assert.throws(() => apply({ kind: 'add-value', name, expression: '0' }), /identifier/);
for (const name of ['try', 'catch'])
  assert(apply({ kind: 'add-value', name, expression: '0' }).includes(`let ${name} = 0`));
assert.throws(() => apply({ kind: 'add-instance', name: 'other', target: 'Add' }), /flow/);
assert.throws(() => apply({ kind: 'connect' }), /Unknown expression flow edit/);
const forged = structuredClone(graph); forged.values.push(graph.values[0]);
assert.throws(() => validateGraphEdit(graph, forged, { kind: 'set-output', expression: '0' }, text, text), /additional statements/);
const duplicate = structuredClone(graph); duplicate.outputs.push(graph.outputs[0]);
assert.throws(() => planEdit(text, duplicate, { kind: 'set-output', expression: '0' }), /duplicate/);
const geometry = scene(graph, {}, 800, 600);
assert.equal(geometry.items.length, 6);
assert.equal(geometry.edges.length, 4);
assert(geometry.edges.every(edge => edge.from && edge.to));
assert.equal(geometry.edges.at(-1).label, 'commit');
assert.equal(geometry.items.find(item => item.kind === 'instance').name, 'child');
console.log('Expression flow edits: Unicode/CRLF, ordinary expressions, child creation, preserved uses, native reparse boundaries and dependency rendering passed.');
