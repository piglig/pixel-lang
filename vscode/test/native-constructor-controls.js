const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Execute the real webview controller with a minimal DOM. Replies are deliberately
// delivered after navigation, including replies already queued by the extension.
const elements = new Map(), messages = [], listeners = new Map();
function element() {
  return { children: [], value: '', textContent: '', options: [], selectedOptions: [],
    append(...items) { this.children.push(...items); this.options.push(...items); },
    replaceChildren(...items) { this.children = [...items]; this.options = [...items]; },
    setAttribute() {}, addEventListener() {}, closest() { return null; } };
}
function get(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); }
class Renderer { draw() {} }
const context = vm.createContext({
  document: { getElementById: get, createElement: element, querySelectorAll: () => [] },
  window: { addEventListener: (name, callback) => listeners.set(name, callback) },
  acquireVsCodeApi: () => ({ postMessage: message => messages.push(message) }),
  PixelNativeGraph: { NativeGraphRenderer: Renderer },
  PixelNativeSpace: { NativeSpaceRenderer: Renderer },
  ResizeObserver: class { observe() {} },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../media/native-graph.js'), 'utf8'), context);
const receive = data => listeners.get('message')({ data });
const graph = { source: 'main.pxl', name: 'Parent', node: 1,
  initialInputs: [{ name: 'start', type: 'int' }], inputs: [{ name: 'value', type: 'int' }],
  values: [], children: [{ node: 2, name: 'child', range: {start:0,end:1}, target: 'Child', inputs: [{ name: 'value', type: 'int' }], result: 'int' }],
  states: [], updates: [], outputs: [], dependencies: [], palette: [{ target: 'Child', kind: 'flow', generic: false, initialInputs: [{ name: 'seed', type: 'int' }] }] };
const state = { type: 'state', epoch: 7, projection: { graphs: [graph], coordinates: [], diagnostics: [] },
  expressionText: { '["main.pxl",2]': 'Child(start + 1)' }, initializerText: { '["main.pxl",2]': ['start + 1'] } };
receive(state);
assert(get('selection').options.some(item => item.textContent === 'Creation parameter · start'));
assert(!get('expression-editor').hidden || get('signature').textContent === '');
get('palette').value = 'Child'; get('palette').onchange();
assert.equal(get('creation-inputs').children[0].textContent, 'seed: int');
get('creation-inputs').children[0].children[0].value = 'start + 3';
get('name').value = 'another'; get('add').onclick();
assert.equal(messages.at(-1).action.kind, 'add-instance');
assert.deepEqual(Array.from(messages.at(-1).action.initializers), ['start + 3']);
assert.equal(messages.at(-1).epoch, 7);
receive(state);
get('selection').value = 'instance:2'; get('selection').onchange();
assert.equal(get('creation-editor').hidden, false);
const input = get('creation-values').children[0].children[0];
assert.equal(input.value, 'start + 1');
assert.equal(get('expression-value').value, 'Child(start + 1)');
input.value = 'start + 8'; get('creation-apply').onclick();
assert.equal(messages.at(-1).action.kind, 'set-initializers');
assert.equal(messages.at(-1).action.node, 2);
assert.deepEqual(Array.from(messages.at(-1).action.initializers), ['start + 8']);
receive(state);
get('selection').value = 'initial:start'; get('selection').onchange();
assert.equal(get('signature').textContent, 'Creation parameter · start: int');
assert.equal(get('creation-editor').hidden, true);
console.log('Native constructor controls: typed creation fields, source values, add/edit messages and separate step ports passed.');
