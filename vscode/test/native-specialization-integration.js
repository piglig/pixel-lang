const vscode = require('vscode'), assert = require('node:assert/strict');

async function run() {
  await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
  await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
  const root = vscode.workspace.workspaceFolders[0].uri;
  const uri = vscode.Uri.joinPath(root, 'main.pxl');
  const library = vscode.Uri.joinPath(root, 'library.pxl');
  await vscode.workspace.fs.writeFile(library, Buffer.from('export fn choose[T](left:T,right:T)->T=left\n'));
  const document = await vscode.workspace.openTextDocument(uri);
  const editor = await vscode.window.showTextDocument(document);
  async function replace(text) {
    assert(await editor.edit(edit => edit.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), text)));
  }
  const prefix = 'import "library.pxl" as library\nrecord Payload {value:int}\neffect fn main(){\n';
  const unfinished = prefix + 'library.choose::[int](7, ';
  await replace(unfinished);
  const signature = await vscode.commands.executeCommand('vscode.executeSignatureHelpProvider', uri, document.positionAt(unfinished.length), ',');
  assert(signature?.signatures.length);
  assert.equal(signature.activeParameter, 1);
  assert.equal(signature.signatures[0].parameters.length, 2);
  const definitions = await vscode.commands.executeCommand('vscode.executeDefinitionProvider', uri, document.positionAt(unfinished.indexOf('choose') + 2));
  assert(definitions.some(definition => (definition.targetUri || definition.uri).toString() === library.toString()));
  const typeArgument = prefix + 'library.choose::[Pay';
  await replace(typeArgument);
  const completions = await vscode.commands.executeCommand('vscode.executeCompletionItemProvider', uri, document.positionAt(typeArgument.length));
  assert(completions.items.some(item => item.label === 'Payload'));
  const complete = prefix + 'let value=library.choose::[int](7,9)\nprint(value)\n}\n';
  await replace(complete);
  const edits = await vscode.commands.executeCommand('vscode.executeFormatDocumentProvider', uri, {tabSize: 4, insertSpaces: true});
  assert(edits.length > 0);
  const formatting = new vscode.WorkspaceEdit();
  formatting.set(uri, edits);
  assert(await vscode.workspace.applyEdit(formatting));
  assert(document.getText().includes('let value = library.choose::[int](7, 9)'));
  const again = await vscode.commands.executeCommand('vscode.executeFormatDocumentProvider', uri, {tabSize: 4, insertSpaces: true});
  assert.equal(again?.length || 0, 0);
  console.log('PASS: explicit specialization signature, cross-file navigation, type completion and formatting in real VS Code.');
}

module.exports = {run};
