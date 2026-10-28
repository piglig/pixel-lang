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
const graph = { source: 'main.pxl', name: 'F', node: 1, initialInputs: [], inputs: [], values: [], children: [], states: [], updates: [], outputs: [], dependencies: [], palette: [] };
receive({ type: 'state', epoch: 1, projection: { graphs: [graph], coordinates: [], diagnostics: [] } });
const runtime = { source: 'main.pxl', declaration: 1, z: 8, start: 0, count: 50, totals: {}, inputs: [], values: [{ name: 'total', phase: 'complete', result: { value: '5' } }], children: [{ name: 'counter', phase: 'skipped' }], states: [{ name: 'saved', retained: true, staged: false, old: { value: '7' }, next: { value: '7' } }, { name: 'changed', retained: false, staged: true, old: { value: '0' }, next: { value: '1' } }], output: { value: 'world', runtime_type: 'Space<World,str,str>', path: ['flow', 1, 'output'] } };
receive({ type: 'runtime', view: runtime });
const runtimeLabels = get('runtime-values').children.map(row => row.children[0].textContent);
assert(runtimeLabels.includes('total · complete: 5'));
assert(runtimeLabels.includes('Child counter: skipped'));
assert(runtimeLabels.includes('saved · unchanged: 7'));
assert(runtimeLabels.includes('changed · staged next: 1'));
assert(!runtimeLabels.includes('saved · next pending: 7'));
function click(label) {
  function find(node) { if (node.textContent === label && node.onclick) return node; for (const child of node.children || []) { const result = find(child); if (result) return result; } }
  const button = find(get('runtime-values')); assert(button, 'Missing button: ' + label); button.onclick();
}
click('Inspect');
receive({ type: 'runtime-children', requestId: messages.at(-1).requestId, path: runtime.output.path, start: 0, variables: [runtime.output] });
click('Space'); const pending = messages.at(-1).requestId;
click('Back to flow');
assert.equal(messages.at(-1).type, 'runtime-cancel');
const currentRows = get('runtime-values').children;
// A stale payload intentionally has no view: accepting it would throw/render it.
receive({ type: 'runtime-space', requestId: pending });
receive({ type: 'runtime-children', requestId: pending });
receive({ type: 'runtime-inspection-error', requestId: pending, message: 'obsolete' });
assert.equal(get('runtime-values').children, currentRows);
assert.notEqual(get('runtime-status').textContent, 'obsolete');
click('Inspect'); const fresh = messages.at(-1).requestId;
assert(fresh > pending);
receive({ type: 'runtime-children', requestId: fresh, path: runtime.output.path, start: 0, variables: [{ name: 'current', value: '7' }] });
assert.notEqual(get('runtime-values').children, currentRows);
assert(get('runtime-values').children.some(row => row.children?.some(child => child.textContent === 'current: 7')));
console.log('Native runtime navigation: queued results/errors cannot reopen abandoned views; fresh inspection still works.');
