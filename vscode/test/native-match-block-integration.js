const vscode = require('vscode'), assert = require('node:assert/strict');

async function run() {
 const api = await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
 await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
 const uri = vscode.Uri.joinPath(vscode.workspace.workspaceFolders[0].uri, 'main.pxl');
 const document = await vscode.workspace.openTextDocument(uri);
 const editor = await vscode.window.showTextDocument(document);
 async function replace(text) {
  assert(await editor.edit(edit => edit.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), text)));
 }
 const source = `// 分支 🛰️
fn value(input:Option[int])->int = match input {
 .Some(n)=>{let doubled=n*2;doubled+1},
 .None=>{let doubled=0;doubled}
}
effect fn report(input:Option[int]) = match input {.Some(n)=>{print(n);},.None=>{}}
effect fn main(){print(value(.Some(2)));report(.None)}
`;
 await replace(source);
 const offset = source.indexOf('doubled+1');
 const definitions = await vscode.commands.executeCommand('vscode.executeDefinitionProvider', uri, document.positionAt(offset));
 assert.equal(definitions.length, 1);
 const definition = definitions[0];
 const range = definition.targetSelectionRange || definition.range;
 assert.equal(document.offsetAt(range.start), source.indexOf('doubled=n'));
 const hover = await vscode.commands.executeCommand('vscode.executeHoverProvider', uri, document.positionAt(offset));
 assert(hover.some(item => item.contents.some(content => /doubled.*int/.test(content.value || content))));
 const refs = await vscode.commands.executeCommand('vscode.executeReferenceProvider', uri, document.positionAt(offset));
 assert(refs.some(item => document.offsetAt(item.range.start) === offset));
 assert(!refs.some(item => document.offsetAt(item.range.start) === source.lastIndexOf('doubled}')));
 const edits = await vscode.commands.executeCommand('vscode.executeFormatDocumentProvider', uri, {tabSize:4,insertSpaces:true});
 assert(edits.length > 0);
 const formatting = new vscode.WorkspaceEdit(); formatting.set(uri, edits);
 assert(await vscode.workspace.applyEdit(formatting));
 assert(document.getText().includes('print(n);'));
 const again = await vscode.commands.executeCommand('vscode.executeFormatDocumentProvider', uri, {tabSize:4,insertSpaces:true});
 assert.equal(again?.length || 0, 0);
 const partial = 'fn value(input:Option[int])->int = match input {.Some(n)=>{let doubled=n*2;dou';
 await replace(partial);
 const completion = await vscode.commands.executeCommand('vscode.executeCompletionItemProvider', uri, document.positionAt(partial.length));
 assert(completion.items.some(item => item.label === 'doubled'));
 const exits = `fn choose(value:Option[int])->int=match value {.Some(n)=>{n+1},.None=>{4}}
fn add(value:inout int,amount:int){value+=amount}
effect fn main(){
 print(choose(.Some(2)));print(choose(.None))
 var total=10
 for n in [1,2,3,4]{
  let choice:Option[int]=.Some(n)
  if n==2{continue}
  if n==4{break}
  add(&total,match choice {.Some(x)=>{x},.None=>0})
 }
 print(total)
}`;
 await replace(exits);
 const exitEdits = await vscode.commands.executeCommand('vscode.executeFormatDocumentProvider', uri, {tabSize:4,insertSpaces:true});
 const exitFormat = new vscode.WorkspaceEdit(); exitFormat.set(uri, exitEdits);
 assert(await vscode.workspace.applyEdit(exitFormat));
 const formatted = document.getText();
 assert(formatted.includes('continue') && formatted.includes('break'));
 const exitAgain = await vscode.commands.executeCommand('vscode.executeFormatDocumentProvider', uri, {tabSize:4,insertSpaces:true});
 assert.equal(exitAgain?.length || 0, 0);
 const built = await api.service().call('build', {files:{'main.pxl':formatted}});
 let executed = built;
 while (!executed.state.halted) executed = await api.service().call('run', {session:built.session});
 assert.deepEqual(executed.state.output, [3,4,14]);
 console.log('PASS: native value blocks, statement loop exits, local inout updates and formatting execute through Studio.');
 console.log('PASS: native match block scope/navigation, hover, unfinished completion and discard-preserving idempotent formatting in real VS Code.');
}

module.exports = {run};
