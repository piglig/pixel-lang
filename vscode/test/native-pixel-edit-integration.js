const vscode = require('vscode');
const assert = require('node:assert/strict');
const path = require('node:path');

async function run() {
  const extension = vscode.extensions.getExtension('pixellang-local.pixellang-studio');
  const api = await extension.activate();
  await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
  const uri = vscode.Uri.file(path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, 'pixel-edit.pxl'));
  const source = '// 猫🌍\neffect fn value() -> int {\n let x = 2 /* keep */ + 3\n print(10)\n print(20)\n return x\n}\neffect fn main() { print(value()) }\n';
  await vscode.workspace.fs.writeFile(uri, Buffer.from(source));
  const doc = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(doc);
  const view = () => api.refreshSpatial('pixel-edit.pxl', uri);
  const target = (data, kind, text) => {
    const item = Object.values(data.targets).find(t => t.kind === kind && t.text.trim() === text);
    assert(item, `${kind}: ${text}`); return item;
  };
  const apply = (data, item, action, value) => api.applySpatial({revision:data.revision, target:item.id, action, value});
  const undo = async () => {
    await vscode.commands.executeCommand('undo');
    assert.equal(doc.getText(), source);
  };
  let data = await view();
  const literal = target(data, 'literal', '2');
  assert(data.regions.some(r => r.tokens.some(t => t.targets.includes(literal.id))));
  await assert.rejects(apply(data, literal, 'literal', '"wrong type"'));
  assert.equal(doc.getText(), source);
  await apply(data, literal, 'literal', '7');
  assert(doc.getText().includes('7 /* keep */ + 3'));
  assert(doc.isDirty);
  assert.equal(Buffer.from(await vscode.workspace.fs.readFile(uri)).toString(), source);
  await undo();
  data = await view();
  await apply(data, target(data, 'binary', '2 /* keep */ + 3'), 'operator', '*');
  assert(doc.getText().includes('(2) /* keep */ * (3)'));
  await undo();
  data = await view();
  await apply(data, target(data, 'print', 'print(20)'), 'up');
  assert(doc.getText().indexOf('print(20)') < doc.getText().indexOf('print(10)'));
  await undo();
  data = await view();
  await assert.rejects(apply(data, target(data, 'return', 'return x'), 'up'), /Unreachable statement/);
  assert.equal(doc.getText(), source);
  const change = new vscode.WorkspaceEdit(); change.insert(uri, doc.positionAt(0), '// Changed after projection\n');
  assert(await vscode.workspace.applyEdit(change));
  const changed = doc.getText();
  await assert.rejects(apply(data, target(data, 'literal', '2'), 'literal', '8'), /Source changed/);
  assert.equal(doc.getText(), changed);
  console.log('PASS: native semantic pixel edits preserve Unicode, comments and undo; reject invalid types, unreachable code and stale snapshots.');
}
module.exports = {run};
