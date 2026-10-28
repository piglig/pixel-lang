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
 const prefix = `// 状态 🛰️
flow Counter(initial:int) step(delta:int)->int {
 state value=initial
 next value=value+delta
 output value
}
effect fn main(){let counter=Counter(0);
`;
 const text = prefix + 'print(counter.step(2))}';
 await replace(text);
 const completions = await vscode.commands.executeCommand('vscode.executeCompletionItemProvider', uri,
  document.positionAt(text.indexOf('counter.step') + 'counter.'.length), '.');
 const step = completions.items.find(item => item.label === 'step');
 assert.equal(step?.kind, vscode.CompletionItemKind.Method);
 assert.equal(step.detail, 'effect fn step(input1: int) -> int');
 assert(!completions.items.some(item => item.label === 'run'));
 const hover = await vscode.commands.executeCommand('vscode.executeHoverProvider', uri,
  document.positionAt(text.indexOf('counter.step') + 'counter.'.length));
 assert(hover.some(item => item.contents.some(content => (content.value || content).includes(step.detail))));
 const incomplete = prefix + 'counter.step(';
 await replace(incomplete);
 let signature = await vscode.commands.executeCommand('vscode.executeSignatureHelpProvider', uri,
  document.positionAt(incomplete.length), '(');
 assert.equal(signature.signatures[0].label, step.detail);
 assert.equal(signature.activeParameter, 0);
 const creation = prefix.slice(0, prefix.indexOf('Counter(0)')) + 'Counter(';
 await replace(creation);
 signature = await vscode.commands.executeCommand('vscode.executeSignatureHelpProvider', uri,
  document.positionAt(creation.length), '(');
 assert.equal(signature.signatures[0].parameters[0].label, 'initial: int');
 assert(!signature.signatures[0].label.includes('delta:'));
 const app = `record Duration{nanoseconds:int}
record Clock{index:int,elapsed:Duration,delta:Duration}
enum Event{Tick}
flow App() step(event:Event,clock:Clock)->int {output clock.index}
effect fn main(){let app=App();`;
 await replace(app + 'app.');
 const methods = await vscode.commands.executeCommand('vscode.executeCompletionItemProvider', uri,
  document.positionAt(document.getText().length), '.');
 assert.equal(methods.items.find(item => item.label === 'run')?.detail,
  'effect fn run(tickEvent: Event, interval: Duration)');
 const runCall = app + 'app.run(Event.Tick, ';
 await replace(runCall);
 signature = await vscode.commands.executeCommand('vscode.executeSignatureHelpProvider', uri,
  document.positionAt(runCall.length), ',');
 assert.equal(signature.activeParameter, 1);
 assert.equal(signature.signatures[0].parameters[1].label, 'interval: Duration');
 console.log('PASS: native flow creation parameters, effectful step completion/hover/signature and clock-specific run in real VS Code.');
}
module.exports = {run};
