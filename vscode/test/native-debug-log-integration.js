const vscode = require('vscode'), fs = require('node:fs'), path = require('node:path');
const assert = require('node:assert/strict');
exports.run = async () => {
  const api = await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
  const config = vscode.workspace.getConfiguration('pixellang');
  await config.update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
  const root = path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, 'debug-log');
  fs.cpSync(process.env.PIXELLANG_TEST_LOGS, root, {recursive:true});
  const fixtures = path.join(root, 'fixtures'), output = path.join(root, 'output'); fs.mkdirSync(output, {recursive:true});
  await config.update('readRoot', fixtures, vscode.ConfigurationTarget.Workspace);
  await config.update('writeRoot', output, vscode.ConfigurationTarget.Workspace);
  const uri = vscode.Uri.file(path.join(root, 'main.pxl')), sourceUri = vscode.Uri.file(path.join(root, 'analyze.pxl'));
  const source = await vscode.workspace.openTextDocument(sourceUri);
  await vscode.window.showTextDocument(source);
  const stops = [];
  const tracker = vscode.debug.registerDebugAdapterTrackerFactory('pixellang', {createDebugAdapterTracker() {
    return {onDidSendMessage(message) {if (message.type === 'event' && message.event === 'stopped') stops.push(message);}};
  }});
  const bridge = api.service(), call = bridge.call.bind(bridge); let spatial = 0;
  bridge.call = (method, ...args) => {if (method === 'spatial-inspect') spatial++; return call(method, ...args);};
  async function waitStop(before) {
    const start = Date.now();
    while (stops.length <= before && Date.now() - start < 30000) await new Promise(resolve => setTimeout(resolve,30));
    assert(stops.length > before, 'Log debugger must stop within 30 seconds');
    console.log('Debug stop latency', Date.now()-start);
  }
  try {
    assert(await vscode.debug.startDebugging(vscode.workspace.workspaceFolders[0], {
      type:'pixellang', request:'launch', name:'Log debug queue regression', program:uri.fsPath,
      stopOnEntry:true, input:fs.readFileSync(path.join(fixtures,'severity-config.json'),'utf8'),
    }));
    await waitStop(0);
    const session = vscode.debug.activeDebugSession;
    const line = source.positionAt(source.getText().indexOf('if checked.rank < minimum')).line+1;
    assert((await session.customRequest('setBreakpoints',{source:{path:sourceUri.fsPath},breakpoints:[{line}]})).breakpoints[0].verified);
    let previous=stops.length; await session.customRequest('continue',{threadId:1}); await waitStop(previous);
    assert.equal((await session.customRequest('stackTrace',{threadId:1})).stackFrames[0].line,line);
    previous=stops.length; await session.customRequest('stepBack',{threadId:1}); await waitStop(previous);
    await session.customRequest('setBreakpoints',{source:{path:sourceUri.fsPath},breakpoints:[]});
    previous=stops.length; await session.customRequest('continue',{threadId:1}); await waitStop(previous);
    assert(api.getCurrent().state.halted && !api.getCurrent().state.error);
    assert.deepEqual(api.getCurrent().state.output, JSON.parse(fs.readFileSync(path.join(fixtures,'severity-expected.json'),'utf8')));
    assert.equal(spatial,0,'Debugger navigation must not launch spatial analysis');
    await vscode.debug.stopDebugging(session);
  } finally {tracker.dispose(); bridge.call=call;}
};
