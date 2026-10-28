const assert = require('node:assert/strict');
const { NativeGraphSession } = require('../src/native-graphs');
const { scene } = require('../media/native-graph-renderer');
// Minimal service fixtures exercise sequencing/fences; native AST round-trips
// are covered by tests/test_expression_flow_projection.py.
const text = 'fn Double(value: int) -> int = value * 2\nflow F(value: int) -> int {\n  let twice = Double(value)\n  output twice\n}\nfn main() {}';
function projectionFor(source) {
 const start = source.indexOf('flow F'), end = source.indexOf('}\nfn main');
 if (start < 0) return { version: 'pixellang-native-graphs/2', graphs: [], coordinates: [], diagnostics: [] };
 const range = (start, length) => ({ start, end: start + length });
 const values = [...source.matchAll(/let (\w+) = ([^\n;]+)/g)].map((m, i) => ({node:11+i,name:m[1],type:'int',range:range(m.index,m[0].length),expression:{range:range(m.index+m[0].length-m[2].length,m[2].length),type:'int'}}));
 const states = [...source.matchAll(/state (\w+): int = ([^\n;]+)/g)].map((m,i)=>({node:30+i,name:m[1],type:'int',range:range(m.index,m[0].length),initializer:range(m.index+m[0].length-m[2].length,m[2].length)}));
 const outputs = [...source.matchAll(/output ([^\n;]+)/g)].map((m,i)=>({node:20+i,range:range(m.index,m[0].length),expression:{range:range(m.index+7,m[1].length),type:'int'}}));
 const graph = {name:'F',source:'main.pxl',node:10,range:range(start,end+1-start),body:range(source.indexOf('{',start),end+1-source.indexOf('{',start)),initialInputs:[],inputs:[{node:1,name:'value',type:'int'}],result:'int',values,children:[],states,updates:[],outputs,palette:[],dependencies:values.length ? [{source:1,target:11,reference:50},{source:11,target:20,reference:51}] : []};
 return {version:'pixellang-native-graphs/2',graphs:[graph],coordinates:[],diagnostics:[]};
}
const projection = projectionFor(text), graph = projection.graphs[0];
function fixture(initialText = text) {
 let content = initialText, imported = 'export fn helper() {}', edited = 0;
 const messages = [];
 const uri = { fsPath: '/project/main.pxl', toString: () => 'file:///project/main.pxl' };
 const document = { uri, version: 1, getText: () => content, positionAt: offset => offset };
 const editor = { document, edit: async (callback, options) => {
   assert.deepEqual(options, { undoStopBefore: true, undoStopAfter: true });
   callback({ replace: (range, value) => { content = content.slice(0, range.start) + value + content.slice(range.end); } });
   edited++; document.version++; return true;
 }, revealRange() {} };
 const vscode = { Uri: { file: () => uri }, ViewColumn: { One: 1 }, Range: class { constructor(start, end) { Object.assign(this, { start, end }); } }, Selection: class {},
   workspace: { openTextDocument: async () => document }, window: { showTextDocument: async () => editor } };
 const collect = async () => ({ root: '/project', entry: 'main.pxl', files: { 'main.pxl': content, 'lib.pxl': imported } });
 const service = { call: async (method, args, options) => { assert.equal(method, 'native-graphs'); if (options) { assert.equal(args.files['main.pxl'], content); assert(options.signal); } return { projection: projectionFor(args.files['main.pxl']) }; } };
 const session = new NativeGraphSession(vscode, collect, () => service, uri, message => messages.push(message));
 return { session, service, messages, document, setImported(value) { imported = value; }, get content() { return content; }, get edited() { return edited; } };
}
(async () => {
 const f = fixture(); await f.session.refresh(); assert.equal(f.messages.at(-1).type, 'state');
 const epoch = f.session.epoch;
 await f.session.edit({ epoch, source: 'main.pxl', graph: 10, action: { kind: 'set-output', expression: 'value' } });
 assert.equal(f.content, text.replace('output twice', 'output value')); assert.equal(f.edited, 1);
 await assert.rejects(f.session.edit({ epoch, source: 'main.pxl', graph: 10, action: {} }), /expired/);
 const changed = fixture(); await changed.session.refresh(); changed.setImported('export fn helper(value: int) {}');
 await assert.rejects(changed.session.edit({ epoch: changed.session.epoch, source: 'main.pxl', graph: 10, action: { kind: 'remove-value', node: 11 } }), /Project sources changed/); assert.equal(changed.edited, 0);
 const race = fixture(); let finish; race.service.call = () => new Promise(resolve => { finish = resolve; });
 const work = race.session.refresh(); await new Promise(resolve => setImmediate(resolve)); race.session.invalidate(); finish({ projection }); await work;
 assert(!race.session.snapshot); assert(!race.messages.some(item => item.type === 'state'));
 const failed = fixture(); failed.service.call = async () => { throw Error('Compiler failed'); }; await failed.session.refresh(); assert(!failed.session.snapshot); assert.equal(failed.messages.at(-1).message, 'Compiler failed');
 const disposed = fixture(); await disposed.session.refresh(); disposed.session.dispose(); assert.throws(() => disposed.session.selection(disposed.session.epoch, 'main.pxl', 10), /expired/);
 const state = fixture(); await state.session.refresh();
 await state.session.edit({ epoch: state.session.epoch, source: 'main.pxl', graph: 10, action: { kind: 'add-state', name: 'previous', type: 'int', initializer: '0' } });
 assert(state.content.includes('state previous: int = 0'));
 assert.equal(state.edited, 1);
 const rejected = fixture(); await rejected.session.refresh();
 rejected.service.call = async () => ({ projection: { ...projection, graphs: [] } });
 await assert.rejects(rejected.session.edit({ epoch: rejected.session.epoch, source: 'main.pxl', graph: 10, action: { kind: 'add-state', name: 'previous', type: 'int', initializer: '0; output value' } }), /preserve the selected flow/);
 assert.equal(rejected.edited, 0);
 const spatialText = '// 星 🛰️\nfn main() { let p = Point3::[World]{x: 1.0, y: 2.0, z: 3.0} }';
 const spatial = fixture(spatialText); await spatial.session.refresh();
 const pointStart = [...spatialText.slice(0, spatialText.indexOf('Point3'))].length;
 const zStart = [...spatialText.slice(0, spatialText.indexOf('3.0'))].length;
 const coordinate = { source: 'main.pxl', node: 99, range: { start: pointStart, end: [...spatialText].length - 2 }, axes: [{ name: 'z', literal: true, range: { start: zStart, end: zStart + 3 } }] };
 spatial.session.snapshot.projection = { ...projection, coordinates: [coordinate] };
 const coordinateEpoch = spatial.session.epoch;
 await spatial.session.revealCoordinate({ epoch: coordinateEpoch, source: 'main.pxl', node: 99 });
 await spatial.session.coordinateEdit({ epoch: coordinateEpoch, source: 'main.pxl', node: 99, values: { z: 5 } });
 assert.equal(spatial.content, spatialText.replace('3.0', '5.0'));
 assert.equal(spatial.edited, 1);
 await assert.rejects(spatial.session.revealCoordinate({ epoch: coordinateEpoch, source: 'main.pxl', node: 99 }), /expired/);
 await assert.rejects(spatial.session.coordinateEdit({ epoch: coordinateEpoch, source: 'main.pxl', node: 99, values: { z: 6 } }), /expired/);
 const spatialRace = fixture(spatialText); await spatialRace.session.refresh();
 spatialRace.session.snapshot.projection = { ...projection, coordinates: [coordinate] }; spatialRace.setImported('changed');
 await assert.rejects(spatialRace.session.coordinateEdit({ epoch: spatialRace.session.epoch, source: 'main.pxl', node: 99, values: { z: 6 } }), /sources changed/);
 assert.equal(spatialRace.edited, 0);
 const a = scene(graph, {}, 900, 600), b = scene(graph, { twice: [1, 2, 4] }, 900, 600);
 assert.equal(a.edges.length, 2); assert(a.edges.every(edge => edge.from && edge.to));
 assert.notEqual(a.items.find(item => item.name === 'twice').x, b.items.find(item => item.name === 'twice').x);
 assert.deepEqual(a.edges.map(item => [item.from.name, item.to.name]), b.edges.map(item => [item.from.name, item.to.name]));
 assert.notEqual(a.items[0].x, scene(graph, {}, 900, 600, 1).items.find(item => item.key === a.items[0].key).x);
 console.log('Native graph controller: source edits, imported-source fences, stale replies, disposal, errors and spatial layout passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
