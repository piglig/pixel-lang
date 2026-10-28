const vscode = require('vscode'), fs = require('node:fs'), path = require('node:path');
const assert = require('node:assert/strict');

exports.run = async () => {
  const api = await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
  await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
  const root = path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, 'diagnostic-project');
  fs.cpSync(process.env.PIXELLANG_TEST_LOGS, root, {recursive: true});
  const modelUri = vscode.Uri.file(path.join(root, 'models.pxl'));
  const analyzeUri = vscode.Uri.file(path.join(root, 'analyze.pxl'));
  const model = await vscode.workspace.openTextDocument(modelUri);
  const analyze = await vscode.workspace.openTextDocument(analyzeUri);
  await vscode.window.showTextDocument(model);
  const original = model.getText();
  const broken = original.replace('minimum_level: Option[string]', 'minimum_level: Option[int]');
  assert.notEqual(broken, original);
  const edit = new vscode.WorkspaceEdit();
  edit.replace(modelUri, new vscode.Range(model.positionAt(0), model.positionAt(original.length)), broken);
  assert(await vscode.workspace.applyEdit(edit));
  const start = Date.now();
  const expected = 'Type shapes do not match at argument.0.0: Expected string, got int';
  let found;
  while (Date.now() - start < 30000) {
    found = vscode.languages.getDiagnostics(analyzeUri).find(d => d.message === expected
      && d.severity === vscode.DiagnosticSeverity.Error
      && analyze.getText(d.range) === 'severity.Minimum(config.minimum_level)');
    if (found) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert(found, 'Cross-file diagnostics must arrive within 30 seconds while automatic outlines are enabled');
  console.log(`Native cross-file diagnostic and exact source span passed in ${Date.now() - start} ms.`);
};
