const vscode = require('vscode'), assert = require('node:assert/strict'), path = require('node:path');
async function waitFor(test, label, milliseconds = 90000) {
  const deadline = Date.now() + milliseconds;
  while (Date.now() < deadline) { if (test()) return; await new Promise(resolve => setTimeout(resolve, 50)); }
  throw Error('Timed out: ' + label);
}
async function run() {
  const api = await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
  await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
  assert((await vscode.commands.getCommands(true)).includes('pixellang.openNativeGraph'));
  const uri = vscode.Uri.file(path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, 'main.pxl'));
  const document = await vscode.workspace.openTextDocument(uri);
  const source = '// 星 🛰️\nfn Double(value: int) -> int = value * 2\nflow Pipeline() step(value: int) -> int {\n let twice = Double(value) // preserved\n output twice\n}\neffect fn main() { print(Pipeline().step(7)) }\n';
  let editor = await vscode.window.showTextDocument(document);
  await editor.edit(builder => builder.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), source));
  const session = await api.nativeGraphs.open(uri);
  await waitFor(() => session.snapshot, 'native graph projection');
  assert.deepEqual(session.snapshot.projection.diagnostics, []);
  const graph = session.snapshot.projection.graphs[0], epoch = session.snapshot.epoch;
  assert.equal(graph.name, 'Pipeline'); assert.equal(graph.values[0].type, 'int');
  assert.equal(session.snapshot.projection.version, 'pixellang-native-graphs/2');
  await session.edit({ epoch, source: 'main.pxl', graph: graph.node, action: { kind: 'set-output', expression: 'value' } });
  assert.equal(document.getText(), source.replace('output twice', 'output value'));
  assert(document.isDirty);
  await waitFor(() => session.snapshot?.project.files['main.pxl'] === document.getText(), 'refreshed graph after edit');
  let output = session.snapshot.projection.graphs[0].outputs[0].expression.range;
  assert.equal([...document.getText()].slice(output.start, output.end).join(''), 'value');
  await assert.rejects(session.edit({ epoch, source: 'main.pxl', graph: graph.node, action: {} }), /expired/);
  console.log('PASS: real native graph source edit, stale action rejection and automatic refresh.');
  await vscode.commands.executeCommand('workbench.action.focusWindow');
  await waitFor(() => vscode.window.state.focused, 'test window focus for undo (unlock the desktop)', 5000);
  editor = await vscode.window.showTextDocument(document, { viewColumn: vscode.ViewColumn.One, preserveFocus: false, preview: false });
  await waitFor(() => vscode.window.activeTextEditor?.document.uri.toString() === uri.toString(), 'source editor selected for undo');
  await vscode.commands.executeCommand('workbench.action.focusActiveEditorGroup');
  await vscode.commands.executeCommand('undo');
  await waitFor(() => document.getText() === source, 'editor undo event');
  assert.equal(document.getText(), source);
  await waitFor(() => session.snapshot?.project.files['main.pxl'] === source, 'graph follows undo');
  output = session.snapshot.projection.graphs[0].outputs[0].expression.range;
  assert.equal([...document.getText()].slice(output.start, output.end).join(''), 'twice');
  const restored = session.snapshot.projection.graphs[0];
  await session.edit({ epoch: session.snapshot.epoch, source: 'main.pxl', graph: restored.node,
    action: { kind: 'add-state', name: 'previous', type: 'int', initializer: '0' } });
  await waitFor(() => session.snapshot?.projection.graphs[0].states.length === 1, 'state added through native compiler');
  const withState = document.getText();
  assert(withState.includes('state previous: int = 0'));
  await vscode.commands.executeCommand('workbench.action.focusActiveEditorGroup');
  await vscode.commands.executeCommand('undo');
  await waitFor(() => document.getText() === source, 'state insertion undo');
  await waitFor(() => session.snapshot?.project.files['main.pxl'] === source, 'state graph follows undo');
  assert.equal(session.snapshot.projection.graphs[0].states.length, 0);
  const constructors = '// 星 🛰️\nflow Child(seed: int) step(value: int) -> int {\n state prior: int = seed\n next prior = value\n output prior\n}\nflow Parent(start: int) step(value: int) -> int {\n instance child = Child(start + 1) // keep\n output child.step(value)\n}\neffect fn main() {}\n';
  editor = await vscode.window.showTextDocument(document);
  await editor.edit(builder => builder.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), constructors));
  await waitFor(() => session.snapshot?.project.files['main.pxl'] === constructors, 'constructor graph projection');
  const parent = () => session.snapshot.projection.graphs.find(item => item.name === 'Parent');
  const editConstructor = initializers => session.edit({ epoch: session.snapshot.epoch, source: 'main.pxl', graph: parent().node,
    action: { kind: 'set-initializers', node: parent().children[0].node, initializers } });
  await assert.rejects(editConstructor(['"wrong"']), /Type mismatch/);
  assert.equal(document.getText(), constructors);
  await editConstructor(['(start + 7)']);
  assert.equal(document.getText(), constructors.replace('Child(start + 1)', 'Child((start + 7))'));
  await vscode.commands.executeCommand('workbench.action.focusActiveEditorGroup');
  await vscode.commands.executeCommand('undo');
  await waitFor(() => document.getText() === constructors, 'constructor edit undo');
  await waitFor(() => session.snapshot?.project.files['main.pxl'] === constructors, 'constructor projection after undo');
  await session.edit({ epoch: session.snapshot.epoch, source: 'main.pxl', graph: parent().node,
    action: { kind: 'add-instance', name: 'another', target: 'Child', initializers: ['start + 2'] } });
  assert(document.getText().includes('instance another = Child(start + 2)'));
  assert(document.getText().includes('Child(start + 1) // keep'));
  await waitFor(() => session.snapshot?.project.files['main.pxl'] === document.getText(),
    'graph projection after adding a configured child');
  assert.equal(parent().children.length, 2);
  console.log('PASS: installed constructor editing, native type rejection, parenthesized expressions, undo and adding an independently configured child.');
  session.dispose();
  console.log('PASS: native command, actual webview, self-hosted projection of unsaved source, real TextEditor edit, expired action rejection, undo and automatic graph refresh.');
}
module.exports = { run };
