const vscode = require('vscode'), assert = require('node:assert/strict'), path = require('node:path');
async function run() {
 await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
 await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
 const uri = vscode.Uri.file(path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, 'main.pxl'));
 const document = await vscode.workspace.openTextDocument(uri);
 const editor = await vscode.window.showTextDocument(document);
 async function replace(text) {
  assert(await editor.edit(builder => builder.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), text)));
 }
 const prefix = `frame World=meters
frame Local=centimeters
record Cost {weight:int}
fn inspect(scene:Space[World,string,Cost],origin:Entity[World,string],transform:Transform3[World,Local]){
`;
 const text = prefix + 'let nearby=scene.neighbors(origin,2.0)\n}\neffect fn main(){}';
 await replace(text);
 const completions = await vscode.commands.executeCommand('vscode.executeCompletionItemProvider', uri,
  document.positionAt(text.indexOf('scene.neighbors') + 'scene.'.length), '.');
 const neighbors = completions.items.find(item => item.label === 'neighbors');
 assert(neighbors);
 assert.equal(neighbors.kind, vscode.CompletionItemKind.Method);
 assert.equal(neighbors.detail, 'fn neighbors(entity: Entity[World, string], radius: float64) -> [Entity[World, string]]');
 const hover = await vscode.commands.executeCommand('vscode.executeHoverProvider', uri, document.positionAt(text.indexOf('neighbors') + 2));
 assert(hover.some(item => item.contents.some(content => (content.value || content).includes(neighbors.detail))));
 const incomplete = prefix + 'scene.insert(Point3::[World]{x:0.0,y:0.0,z:0.0}, ';
 await replace(incomplete);
 const signature = await vscode.commands.executeCommand('vscode.executeSignatureHelpProvider', uri, document.positionAt(incomplete.length), ',');
 assert.equal(signature.signatures[0].label, 'fn insert(position: Point3[World], payload: string) -> Space[World, string, Cost]');
 assert.equal(signature.activeParameter, 1);
 const transform = prefix + 'transform.';
 await replace(transform);
 const methods = await vscode.commands.executeCommand('vscode.executeCompletionItemProvider', uri, document.positionAt(transform.length), '.');
 assert.equal(methods.items.find(item => item.label === 'then')?.detail,
  'fn then[Target](after: Transform3[Local, Target]) -> Transform3[World, Target]');
 console.log('PASS: native spatial method completion, hover, unfinished-call signature and transform frame relations in real VS Code.');
}
module.exports = {run};
