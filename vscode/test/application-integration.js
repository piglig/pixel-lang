const vscode=require('vscode'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
async function run() {
  const extension=vscode.extensions.getExtension('pixellang-local.pixellang-studio'),api=await extension.activate();
  await vscode.workspace.getConfiguration('pixellang').update('pythonPath',process.env.PIXELLANG_TEST_PYTHON,vscode.ConfigurationTarget.Workspace);
  assert((await vscode.commands.getCommands(true)).includes('pixellang.openApplication'));
  assert((await vscode.commands.getCommands(true)).includes('pixellang.openApplicationJournal'));
  const root=vscode.workspace.workspaceFolders[0].uri.fsPath;
  for(const name of ['native-orders']) fs.cpSync(path.resolve(__dirname,'../../examples',name),path.join(root,name),{recursive:true});
  const order=await api.application.open(vscode.Uri.file(path.join(root,'native-orders/main.pxl'))),session=order.session;
  await api.service().call('application-step',{session});
  await api.service().call('application-input',{session,valueText:'{"variant":"Pay","fields":{"cents":1299}}'});
  assert.equal((await api.service().call('application-step',{session})).view.order.status.variant,'Paid');
  const recording=await api.service().call('application-recording',{session});
  await api.service().call('application-seek',{session,index:1});
  assert.equal((await api.service().call('application-status',{session})).view.order.status.variant,'AwaitingPayment');
  assert.equal((await api.service().call('application-restore',{session,...recording})).view.order.status.variant,'Paid');
  let stoppedResolve;const stopped=new Promise(resolve=>stoppedResolve=resolve);
  const tracker=vscode.debug.registerDebugAdapterTrackerFactory('pixellang',{createDebugAdapterTracker(){return {onDidSendMessage(message){if(message.type==='event'&&message.event==='stopped')stoppedResolve(message);}};}});
  assert(await vscode.debug.startDebugging(undefined,{type:'pixellang',request:'launch',name:'Native application event',
    program:path.join(root,'native-orders/main.pxl'),applicationSession:session,eventIndex:2,stopOnEntry:true}));
  let timer;await Promise.race([stopped,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Native event debugger did not stop')),30000);})]);clearTimeout(timer);
  const debug=vscode.debug.activeDebugSession;assert(debug);
  const stack=await debug.customRequest('stackTrace',{threadId:1});assert(stack.stackFrames.some(frame=>frame.name.includes('Order')));
  await debug.customRequest('next',{threadId:1});await vscode.debug.stopDebugging(debug);tracker.dispose();
  assert.equal((await api.service().call('application-status',{session})).cursor,2);
  await api.service().call('application-input',{session,valueText:'{"variant":"Pay","fields":{"cents":1}}'});
  const rejected=await api.service().call('application-step',{session});
  assert.equal(rejected.view.order.status.variant,'Paid');
  assert.equal(rejected.view.outcome.variant,'Err');
  assert.equal(rejected.view.outcome.fields.error.variant,'InvalidTransition');
  assert.equal(rejected.cursor,3);
  await api.service().call('application-input',{session,valueText:'{"variant":"Ship","fields":{"tracking":"PK-001"}}'});
  assert.equal((await api.service().call('application-step',{session})).view.order.status.variant,'Shipped');
  const journalPath=path.join(root,'orders.pxjournal');
  const journal=await api.application.open(vscode.Uri.file(path.join(root,'native-orders/main.pxl')),{journalPath});
  assert.equal(journal.journal,journalPath);
  await api.service().call('application-step',{session:journal.session});
  await api.service().call('application-input',{session:journal.session,valueText:'{"variant":"Pay","fields":{"cents":1299}}'});
  assert.equal((await api.service().call('application-step',{session:journal.session})).view.order.status.variant,'Paid');
  for(let i=0;i<130;i++)await api.service().call('application-step',{session:journal.session});
  const journalDebug=await api.service().call('application-debug',{session:journal.session,index:130});
  assert(journalDebug.session);
  await api.service().call('application-seek',{session:journal.session,index:1});
  assert.equal((await api.service().call('application-status',{session:journal.session})).view.order.status.variant,'AwaitingPayment');
  await api.service().call('application-close',{session:journal.session});
  const resumed=await api.application.open(vscode.Uri.file(path.join(root,'native-orders/main.pxl')),{journalPath});
  assert.equal(resumed.cursor,132);assert.equal(resumed.view.order.status.variant,'Paid');
  if (Number(process.env.PIXELLANG_TEST_HOLD_MS || 0)) {
    await api.application.open(vscode.Uri.file(path.join(root,'native-orders/main.pxl')));
    await new Promise(resolve=>setTimeout(resolve,Number(process.env.PIXELLANG_TEST_HOLD_MS)));
  }
  console.log('PASS: Native application panels, typed events, recording/replay, isolated source debugger and durable journals');
}
module.exports={run};
