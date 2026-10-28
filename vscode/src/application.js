const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');

function registerApplication(vscode, context, service) {
  const open = async (uri, {journalPath} = {}) => {
    if (!uri) {
      uri = vscode.window.activeTextEditor?.document.uri;
      if (!uri || !/\.(pxl|png)$/i.test(uri.fsPath)) {
        const picked = await vscode.window.showOpenDialog({canSelectMany:false, filters:{'PixelLang application':['pxl','png']}});
        if (!picked) return;
        uri = picked[0];
      }
    }
    if (!await vscode.workspace.saveAll(false)) throw Error('Save the project before opening its application.');
    const backend = service();
    let current = await backend.call('application-open', {path:uri.fsPath, configuration:vscode.workspace.getConfiguration('pixellang', uri).get('application', {}), ...(journalPath ? {journalPath} : {})}), disposed = false;
    let panel;
    try {
      panel = vscode.window.createWebviewPanel('pixellang.application', 'PixelLang · ' + path.basename(uri.fsPath) + (journalPath ? ' · Journal' : ''), vscode.ViewColumn.Active,
        {enableScripts:true, retainContextWhenHidden:true, localResourceRoots:[vscode.Uri.joinPath(context.extensionUri,'media')]});
      const nonce = crypto.randomBytes(16).toString('hex');
      let html = fs.readFileSync(path.join(context.extensionPath,'media/application.html'),'utf8')
        .replaceAll('{{csp}}',panel.webview.cspSource).replaceAll('{{nonce}}',nonce);
      for (const [key,name] of Object.entries({css:'application.css',script:'application.js',renderer:'native-space-renderer.js'}))
        html = html.replace('{{'+key+'}}',panel.webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri,'media',name)).toString());
      let queue = Promise.resolve();
      const receive = async message => {
        if (disposed) return;
        try {
          if (message.type === 'ready') { await panel.webview.postMessage({type:'state',value:current}); return; }
          if (message.type === 'debug') {
            await vscode.debug.startDebugging(undefined,{type:'pixellang',request:'launch',name:'PixelLang application event',
              program:uri.fsPath,applicationSession:current.session,eventIndex:current.cursor,stopOnEntry:true});
          } else if (message.type === 'save') {
            const target = await vscode.window.showSaveDialog({filters:{'PixelLang recording':['pxevents']}});
            if (target) {
              const data = await backend.call('application-recording',{session:current.session});
              await vscode.workspace.fs.writeFile(target,Buffer.from(data.recordingText+'\n'));
            }
          } else if (message.type === 'load') {
            const selected = await vscode.window.showOpenDialog({canSelectMany:false,filters:{'PixelLang recording':['pxevents']}});
            if (selected) {
              const info = await vscode.workspace.fs.stat(selected[0]);
              if (info.size > 64000000) throw Error('Recording exceeds 64 MB.');
              current = await backend.call('application-restore',{session:current.session,
                recordingText:Buffer.from(await vscode.workspace.fs.readFile(selected[0])).toString('utf8')});
            }
          } else {
            const methods = {step:'application-step',seek:'application-seek',input:'application-input',discard:'application-discard'};
            if (!methods[message.type]) return;
            current = await backend.call(methods[message.type],{...message,session:current.session});
          }
          if (!disposed) await panel.webview.postMessage({type:'state',value:current});
        } catch(error) {
          if (!disposed) await panel.webview.postMessage({type:'error',message:error.message});
        }
      };
      panel.webview.onDidReceiveMessage(message => { queue = queue.then(()=>receive(message)); });
      panel.onDidChangeViewState(event => {
        if (!event.webviewPanel.visible) panel.webview.postMessage({type:'pause'});
      });
      panel.webview.html = html;
      panel.onDidDispose(()=>{ disposed=true; queue.then(()=>backend.call('application-close',{session:current.session})).catch(()=>{}); });
      context.subscriptions.push(panel);
      return current;
    } catch (error) {
      disposed = true;
      panel?.dispose();
      await backend.call('application-close', {session:current.session}).catch(()=>{});
      throw error;
    }
  };
  context.subscriptions.push(vscode.commands.registerCommand('pixellang.openApplication',async uri=>{
    try { return await open(uri); } catch(error) { vscode.window.showErrorMessage(error.message); }
  }));
  context.subscriptions.push(vscode.commands.registerCommand('pixellang.openApplicationJournal', async uri => {
    try {
      const action = await vscode.window.showQuickPick(['Create journal', 'Resume journal'], {placeHolder:'Choose application history'});
      if (!action) return;
      let target;
      if (action === 'Create journal') {
        target = await vscode.window.showSaveDialog({filters:{'PixelLang journal':['pxjournal']}, saveLabel:'Create journal'});
        if (target && fs.existsSync(target.fsPath)) throw Error('A journal already exists here. Choose Resume journal to open it.');
      } else {
        target = (await vscode.window.showOpenDialog({canSelectMany:false,filters:{'PixelLang journal':['pxjournal']}}))?.[0];
      }
      if (target) return await open(uri, {journalPath:target.fsPath});
    } catch(error) { vscode.window.showErrorMessage(error.message); }
  }));
  return {open};
}
module.exports = {registerApplication};
