const vscode = require('vscode'), assert = require('node:assert/strict'), path = require('node:path');
async function waitFor(test, label) {
 const deadline = Date.now() + 90000;
 while (Date.now() < deadline) { if (test()) return; await new Promise(resolve => setTimeout(resolve, 50)); }
 throw Error('Timed out: ' + label);
}
async function run() {
 const api = await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
 await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
 const uri = vscode.Uri.file(path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, 'main.pxl'));
 const document = await vscode.workspace.openTextDocument(uri);
 const source = '// 星 🛰️\nflow Counter() step(delta:int)->int {\n state value=7\n next value=value+delta // keep\n output value\n}\neffect fn main(){let counter=Counter();print(counter.step(2));print(counter.step(3))}\n';
 const editor = await vscode.window.showTextDocument(document);
 assert(await editor.edit(builder => builder.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), source)));
 const session = await api.nativeGraphs.open(uri);
 await waitFor(() => session.snapshot?.project.files['main.pxl'] === source, 'initial native projection');
 assert.deepEqual(session.snapshot.projection.diagnostics, []);
 const graph = session.snapshot.projection.graphs[0], epoch = session.snapshot.epoch;
 await session.edit({ epoch, source: 'main.pxl', graph: graph.node,
  action: { kind: 'remove-next', node: graph.updates[0].node } });
 const retained = document.getText();
 assert(retained.includes('// 星 🛰️\n'));
 assert(retained.includes('// keep\n'));
 assert(retained.includes('state value=7'));
 assert(!retained.includes('next value'));
 await waitFor(() => session.snapshot?.project.files['main.pxl'] === retained, 'retained-state graph refresh');
 assert.deepEqual(session.snapshot.projection.diagnostics, []);
 assert.equal(session.snapshot.projection.graphs[0].updates.length, 0);
 assert.equal(session.snapshot.projection.graphs[0].states.length, 1);
 await assert.rejects(session.edit({ epoch, source: 'main.pxl', graph: graph.node,
  action: { kind: 'remove-next', node: graph.updates[0].node } }), /expired/);
 await api.run(uri);
 assert.deepEqual(api.getCurrent().output, ['7', '7']);
 const current = session.snapshot.projection.graphs[0];
 await session.edit({ epoch: session.snapshot.epoch, source: 'main.pxl', graph: current.node,
  action: { kind: 'set-next', state: 'value', expression: 'value+delta' } });
 await waitFor(() => session.snapshot?.project.files['main.pxl'] === document.getText() && session.snapshot.projection.graphs[0].updates.length === 1, 'explicit update restored');
 assert.deepEqual(session.snapshot.projection.diagnostics, []);
 await api.run(uri);
 assert.deepEqual(api.getCurrent().output, ['7', '9']);
 session.dispose();
 console.log('PASS: real VS Code removes/restores next through native graph edits, preserves Unicode/comments, rejects stale edits, and executes retained/updated state.');
}
module.exports = { run };
