const vscode = require('vscode');
const assert = require('node:assert/strict');
async function waitFor(predicate, label) {
  const deadline = Date.now() + 30000;
  while (!predicate() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50));
  assert(predicate(), label);
}
async function undo(document, expected) {
  await vscode.commands.executeCommand('workbench.action.focusWindow');
  await waitFor(() => vscode.window.state.focused, 'Real undo requires focused test window');
  await vscode.window.showTextDocument(document, {preview: false, preserveFocus: false});
  await vscode.commands.executeCommand('workbench.action.focusActiveEditorGroup');
  await vscode.commands.executeCommand('undo');
  await waitFor(() => document.getText() === expected, 'Undo restores exact source text');
}
async function run() {
  const api = await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
  await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
  const root = vscode.workspace.workspaceFolders[0].uri;
  await vscode.workspace.fs.writeFile(vscode.Uri.joinPath(root, 'pixel.toml'), Buffer.from('format = 1\n[project]\nname = "undo-acceptance"\nentry = "main.pxl"\n'));
  const modelUri = vscode.Uri.joinPath(root, 'model.pxl');
  const mainUri = vscode.Uri.joinPath(root, 'main.pxl');
  const modelText = 'export record Row { amount: int }\n';
  const mainText = 'import "model.pxl" as model\neffect fn main() { let row = model.Row{amount: 10}; print(row.amount) }\n';
  await vscode.workspace.fs.writeFile(modelUri, Buffer.from(modelText));
  await vscode.workspace.fs.writeFile(mainUri, Buffer.from(mainText));
  const model = await vscode.workspace.openTextDocument(modelUri);
  const main = await vscode.workspace.openTextDocument(mainUri);
  await vscode.window.showTextDocument(model);
  const edit = new vscode.WorkspaceEdit();
  edit.replace(modelUri, new vscode.Range(model.positionAt(0), model.positionAt(modelText.length)), modelText.replace('int', 'string'));
  assert(await vscode.workspace.applyEdit(edit));
  await waitFor(() => vscode.languages.getDiagnostics(mainUri).some(d => d.severity === vscode.DiagnosticSeverity.Error), 'Cross-file type error appears');
  await undo(model, modelText);
  await waitFor(() => !vscode.languages.getDiagnostics(mainUri).some(d => d.severity === vscode.DiagnosticSeverity.Error), 'Cross-file type error clears after undo');
  console.log('Undo: source edit and cross-file diagnostics restored.');
  const rename = await vscode.commands.executeCommand('vscode.executeDocumentRenameProvider', modelUri, model.positionAt(modelText.indexOf('amount')), 'total');
  assert(rename.get(modelUri).length > 0 && rename.get(mainUri).length > 0);
  assert(await vscode.workspace.applyEdit(rename));
  assert(main.getText().includes('row.total'));
  await undo(model, modelText);
  assert.equal(main.getText(), mainText, 'A single undo restores both renamed files');
  console.log('Undo: multi-file rename restored atomically.');
  await vscode.window.showTextDocument(main);
  const spatial = await api.refreshSpatial('main.pxl', mainUri);
  const target = Object.values(spatial.targets).find(t => t.kind === 'literal' && t.text === '10');
  assert(target);
  await api.applySpatial({revision: spatial.revision, target: target.id, action: 'literal', value: '20'});
  assert(main.getText().includes('20'));
  await undo(main, mainText);
  console.log('PASS: real VS Code undo restores text, diagnostics, multi-file rename and spatial source edit.');
}
module.exports = {run};
