const vscode = require('vscode');
const assert = require('node:assert/strict');
const path = require('node:path');
async function run() {
  const extension = vscode.extensions.getExtension('pixellang-local.pixellang-studio');
  const api = await extension.activate();
  await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
  const uri = vscode.Uri.file(path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, 'format.pxl'));
  await vscode.workspace.fs.writeFile(uri, Buffer.from('fn main() {}\n'));
  const doc = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(doc);
  const source = '// 世界\nimport "missing.pxl" as lib\nfn main(){\nlet text="}"\nlib.use(text)\n}\n';
  const edit = new vscode.WorkspaceEdit();
  edit.replace(uri, new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length)), source);
  assert(await vscode.workspace.applyEdit(edit));
  assert(doc.isDirty);
  const expected = await api.service().call('format', {entry:'format.pxl', files:{'format.pxl':source}});
  const edits = await vscode.commands.executeCommand('vscode.executeFormatDocumentProvider', uri, {});
  assert(edits.length > 0);
  const apply = new vscode.WorkspaceEdit(); apply.set(uri, edits);
  assert(await vscode.workspace.applyEdit(apply));
  assert.equal(doc.getText(), expected.files['format.pxl']);
  assert.match(doc.getText(), /    let text = "}"/);
  const again = await vscode.commands.executeCommand('vscode.executeFormatDocumentProvider', uri, {});
  assert.equal(again?.length || 0, 0);
  assert.equal(Buffer.from(await vscode.workspace.fs.readFile(uri)).toString(), 'fn main() {}\n');
  // Hold a genuine service response while the user changes the document.
  const service = api.service(), original = service.call;
  let release, observed;
  const held = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { observed = resolve; });
  service.call = async function(method, args, options) {
    const result = await original.call(this, method, args, options);
    if (method === 'format') { observed(); await held; }
    return result;
  };
  try {
    const pending = vscode.commands.executeCommand('vscode.executeFormatDocumentProvider', uri, {});
    await started;
    const change = new vscode.WorkspaceEdit();
    change.insert(uri, doc.positionAt(0), '// New edit while formatting\n');
    assert(await vscode.workspace.applyEdit(change));
    release();
    const stale = await pending;
    assert.equal(stale?.length || 0, 0);
    assert(doc.getText().startsWith('// New edit while formatting\n'));
  } finally {
    release(); service.call = original;
  }
  console.log('PASS: native Studio formatting uses unsaved text, allows missing imports, is idempotent and rejects stale replies.');
}
module.exports = {run};
