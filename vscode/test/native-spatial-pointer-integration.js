// Human/CUA pointer and keyboard acceptance. This suite only sets up debugging
// and observes document changes; it never synthesizes the drag or undo command.
const vscode = require('vscode'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {source} = require('./native-spatial-debug-integration');
async function waitFor(test, label, timeout = 90000) {
 const deadline = Date.now()+timeout;
 while(Date.now()<deadline) {if(test()) return; await new Promise(resolve=>setTimeout(resolve,50));}
 throw Error('Timed out: '+label);
}
async function run() {
 const reportPath=process.env.PIXELLANG_INTERACTION_REPORT;
 assert(reportPath,'Provide PIXELLANG_INTERACTION_REPORT');
 const evidence={source};
 const report=phase=>{evidence.phase=phase;fs.writeFileSync(reportPath,JSON.stringify(evidence,null,2));console.log('Pointer acceptance: '+phase);};
 const api=await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
 await vscode.workspace.getConfiguration('pixellang').update('pythonPath',process.env.PIXELLANG_TEST_PYTHON,vscode.ConfigurationTarget.Workspace);
 const uri=vscode.Uri.file(path.join(vscode.workspace.workspaceFolders[0].uri.fsPath,'main.pxl'));
 const document=await vscode.workspace.openTextDocument(uri);
 const editor=await vscode.window.showTextDocument(document,{viewColumn:vscode.ViewColumn.One,preview:false});
 assert(await editor.edit(builder=>builder.replace(new vscode.Range(document.positionAt(0),document.positionAt(document.getText().length)),source)));
 await document.save();
 const panel=await api.nativeGraphs.open(uri);
 await waitFor(()=>panel.snapshot,'native projection');
 assert.deepEqual(panel.snapshot.projection.diagnostics,[]);
 const stops=[],outputs=[];
 const tracker=vscode.debug.registerDebugAdapterTrackerFactory('pixellang',{createDebugAdapterTracker(session){return{onDidSendMessage(message){
  if(message.type!=='event')return;
  if(message.event==='stopped')stops.push(session.id);
  if(message.event==='output')outputs.push(message.body.output);
 }};}});
 let debug;
 try{
  assert(await vscode.debug.startDebugging(undefined,{type:'pixellang',request:'launch',name:'Pointer acceptance',program:uri.fsPath,stopOnEntry:true}));
  debug=vscode.debug.activeDebugSession;
  await waitFor(()=>stops.includes(debug.id),'entry pause');stops.length=0;
  const breaks=await debug.customRequest('setBreakpoints',{source:{path:uri.fsPath},breakpoints:[{line:4}]});assert(breaks.breakpoints[0].verified);
  await debug.customRequest('continue',{threadId:1});await waitFor(()=>stops.includes(debug.id),'space input pause');
  const paused=await debug.customRequest('pixelFlowFrames');
  evidence.pause={status:paused,output:outputs,current:api.getCurrent()};
  assert(paused.frames.length>0,'The breakpoint must pause inside an active flow');
  try { await waitFor(()=>panel.debug.current,'graph receives paused input'); }
  catch(error) {
   const status=await debug.customRequest('pixelFlowFrames');
   evidence.overlay={debugProject:status.project,frames:status.frames,
    projectionProject:panel.snapshot?.project,graphs:panel.snapshot?.projection.graphs.map(graph=>({source:graph.source,node:graph.node}))};
   throw error;
  }
  evidence.workspace=uri.fsPath;report('awaiting-pointer-drag');
  // Select Inspect space in the webview, select entity #1, choose Move Z and
  // drag that entity with the desktop pointer. No source-changing test API call.
  await waitFor(()=>document.getText()!==source,'actual pointer edit',600000);
  const edited=document.getText();
  const match=edited.match(/let near = Point3::\[World\]\{x: 0\.0, y: 0\.0, z: ([^}]+)\}/);
  assert(match,'Edited position expression retained');
  assert(Number.isFinite(Number(match[1])) && Number(match[1])!==2);
  assert.equal(edited,source.replace('z: 2.0','z: '+match[1]));
  assert(document.isDirty);
  await waitFor(()=>panel.snapshot?.project.files['main.pxl']===edited,'graph refreshes changed source');
  await panel.debug.refresh();assert.equal(panel.debug.current,undefined,'Stale debug view must be rejected');
  evidence.edited=edited;evidence.z=Number(match[1]);report('awaiting-keyboard-undo');
  // Focus the source editor and press the platform undo shortcut via the UI.
  await waitFor(()=>document.getText()===source,'actual keyboard undo',600000);
  await waitFor(()=>panel.snapshot?.project.files['main.pxl']===source,'graph follows keyboard undo');
  assert.deepEqual(panel.snapshot.projection.diagnostics,[]);
  evidence.restored=document.getText();report('verified');
  console.log('PASS: desktop pointer changed only the native Z literal; keyboard undo restored source and projection.');
 }catch(error){evidence.error=String(error);report('failed');throw error;}
 finally{if(debug)await vscode.debug.stopDebugging(debug);tracker.dispose();panel.dispose();}
}
module.exports={run};
