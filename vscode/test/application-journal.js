const assert=require('node:assert/strict'), path=require('node:path');
const {registerApplication}=require('../src/application');
(async()=>{
  const calls=[],commands=new Map(),messages=[];
  let receive,dispose;
  const configuration={intervalNanoseconds:200000000,tick:{variant:"Tick",fields:{}}};
  const backend={call:async(method,args)=>{calls.push({method,args});return method==='application-open'?{session:'journal-session',journal:args.journalPath,cursor:0,length:0}:{};}};
  const panel={webview:{cspSource:'test',asWebviewUri:x=>({toString:()=>x.fsPath}),postMessage:async x=>messages.push(x),
    onDidReceiveMessage:fn=>{receive=fn;}},onDidChangeViewState:()=>{},onDidDispose:fn=>{dispose=fn;},dispose:()=>dispose?.()};
  const vscode={Uri:{joinPath:(base,...parts)=>({fsPath:path.join(base.fsPath,...parts)})},ViewColumn:{Active:1},
    workspace:{saveAll:async()=>true,getConfiguration:(section,uri)=>{
      assert.equal(section,'pixellang');assert.equal(uri.fsPath,'/project/main.pxl');
      return {get:(key)=>{assert.equal(key,'application');return configuration;}};
    }},commands:{registerCommand:(name,fn)=>{commands.set(name,fn);return {dispose(){}};}},
    window:{createWebviewPanel:()=>panel,showErrorMessage:()=>{}}};
  const context={extensionPath:path.resolve(__dirname,'..'),extensionUri:{fsPath:path.resolve(__dirname,'..')},subscriptions:[]};
  const api=registerApplication(vscode,context,()=>backend);
  assert(commands.has('pixellang.openApplicationJournal'));
  await api.open({fsPath:'/project/main.pxl'},{journalPath:'/project/orders.pxjournal'});
  assert.deepEqual(calls[0],{method:'application-open',args:{path:'/project/main.pxl',configuration,journalPath:'/project/orders.pxjournal'}});
  receive({type:'ready'});await new Promise(setImmediate);
  assert.equal(messages[0].value.journal,'/project/orders.pxjournal');
  dispose();await new Promise(setImmediate);
  assert.equal(calls.at(-1).method,'application-close');
  vscode.window.createWebviewPanel=()=>{throw Error('panel failed');};
  await assert.rejects(api.open({fsPath:'/project/main.pxl'},{journalPath:'/project/orders.pxjournal'}),/panel failed/);
  assert.equal(calls.at(-1).method,'application-close');
  console.log('Application journal routing, panel disposal and failed-open cleanup passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
