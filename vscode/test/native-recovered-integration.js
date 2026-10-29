// Exercise the recovered tutorial source without reconstructing its declarations.
const vscode=require('vscode'), assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
async function waitFor(test,label,timeout=90000){const until=Date.now()+timeout;while(Date.now()<until){if(test())return;await new Promise(resolve=>setTimeout(resolve,40));}throw Error('Timed out: '+label);}
async function run(){
 assert(process.env.PIXELLANG_RECOVERED_SOURCE,'Provide the recovered native-space tutorial source with cost=9');
 const source=fs.readFileSync(process.env.PIXELLANG_RECOVERED_SOURCE,'utf8');
 // Semantic PNG recovery may synthesize names when source metadata is absent.
 // Locate identities from the recovered declarations, not the original text.
 const frameName=source.match(/^frame (\w+) = meters/m)?.[1];
 const recordName=source.match(/^record (\w+) \{ cost: int[, }]/m)?.[1];
 const functionName=source.match(/^fn (\w+)\(/m)?.[1];
 const flowName=source.match(/^flow (\w+)\(/m)?.[1];
 const roadName=source.match(/(\w+)\.payload\.cost/)?.[1];
 const flowCall=source.match(new RegExp('print\\('+flowName+'\\(\\)\\.step\\((\\w+)\\)\\)'));
 assert(frameName&&recordName&&functionName&&flowName&&roadName&&flowCall);
 const line=source.split('\n').findIndex(text=>/return len\(\w+\.neighbors\(/.test(text))+1;assert(line>0);
 const api=await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
 await vscode.workspace.getConfiguration('pixellang').update('pythonPath',process.env.PIXELLANG_TEST_PYTHON,vscode.ConfigurationTarget.Workspace);
 const uri=vscode.Uri.file(path.join(vscode.workspace.workspaceFolders[0].uri.fsPath,'main.pxl'));
 const document=await vscode.workspace.openTextDocument(uri), editor=await vscode.window.showTextDocument(document);
 assert(await editor.edit(builder=>builder.replace(new vscode.Range(document.positionAt(0),document.positionAt(document.getText().length)),source)));
 await document.save();
 // Exercise the installed language providers on real PNG-recovered native
 // syntax. A successful graph/DAP test alone can miss background provider errors.
 const position=needle=>{const offset=source.indexOf(needle);assert(offset>=0,needle);return document.positionAt(offset);};
 const symbols=await vscode.commands.executeCommand('vscode.executeDocumentSymbolProvider',uri);
 for(const name of [frameName,recordName,functionName,flowName])assert(symbols.some(symbol=>symbol.name===name),name);
 const hovers=await vscode.commands.executeCommand('vscode.executeHoverProvider',uri,position(roadName+'.payload'));
 assert(hovers.some(hover=>hover.contents.some(content=>(content.value||String(content)).includes(`Link[${frameName}, string, ${recordName}]`))));
 const definitions=await vscode.commands.executeCommand('vscode.executeDefinitionProvider',uri,position('cost)'));
 assert.equal(definitions.length,1);
 assert.equal(document.offsetAt(definitions[0].range.start),source.indexOf('cost: int'));
 const completion=await vscode.commands.executeCommand('vscode.executeCompletionItemProvider',uri,position('cost)'),'.');
 assert(completion.items.some(item=>item.label==='cost'&&item.detail==='int'));
 const signature=await vscode.commands.executeCommand('vscode.executeSignatureHelpProvider',uri,position(flowCall[1]+'))'),'(');
 assert(signature.signatures.some(item=>item.label.includes(`fn step(`)&&item.label.includes(`Space[${frameName}, string, ${recordName}]`)));
 const rename=await vscode.commands.executeCommand('vscode.executeDocumentRenameProvider',uri,position(recordName+' {'),'Route');
 const edits=rename.entries().flatMap(([,items])=>items);
 assert(edits.length>=5);assert(edits.every(edit=>edit.newText==='Route'));
 assert.equal(document.getText(),source,'Rename preview must not mutate the document');
 const panel=await api.nativeGraphs.open(uri);
 await waitFor(()=>panel.snapshot,'recovered native projection');
 assert.deepEqual(panel.snapshot.projection.diagnostics,[]);
 assert.equal(panel.snapshot.projection.graphs.length,1);
 const stops=[],outputs=[];
 const tracker=vscode.debug.registerDebugAdapterTrackerFactory('pixellang',{createDebugAdapterTracker(session){return{onDidSendMessage(message){
  if(message.type!=='event')return;
  if(message.event==='stopped')stops.push(session.id);
  if(message.event==='output' && /^-?\d+(\.\d+)?$/.test(message.body.output.trim()))outputs.push(Number(message.body.output.trim()));
 }};}});
 let debug;
 async function stop(){await waitFor(()=>stops.includes(debug.id),'debug pause');stops.splice(stops.indexOf(debug.id),1);}
 async function launch(){
  assert(await vscode.debug.startDebugging(undefined,{type:'pixellang',request:'launch',name:'Recovered spatial program',program:uri.fsPath,stopOnEntry:true}));
  debug=vscode.debug.activeDebugSession;await stop();
  const breaks=await debug.customRequest('setBreakpoints',{source:{path:uri.fsPath},breakpoints:[{line}]});assert(breaks.breakpoints[0].verified);
  await debug.customRequest('continue',{threadId:1});await stop();
  const status=await debug.customRequest('pixelFlowFrames');assert.equal(status.frames.length,1);
  const args={revision:status.revision,frame:status.frames[0].frame};
  const graph=await debug.customRequest('pixelFlowGraph',args);assert.equal(graph.inputs.length,1);
  const input=graph.inputs[0].value;
  const view=await debug.customRequest('pixelFlowSpace',{...args,path:input.path});
  return {args,input,view};
 }
 try{
  const before=await launch();assert.equal(before.view.entities[1].position[2],1.5);
  assert.equal(before.view.linkTotal,1);
  const payload=await debug.customRequest('pixelFlowChildren',{...before.args,path:before.view.links[0].payload.path,start:0,count:50});
  assert.equal(payload.variables.find(item=>item.name==='cost').value,'9');
  const origin=before.view.entities[1].origin;assert(origin);
  await panel.coordinateEdit({epoch:panel.snapshot.epoch,source:origin.source,node:origin.node,values:{z:2.5}});
  assert.equal(document.getText(),source.replace('z: 1.5','z: 2.5'));
  await vscode.debug.stopDebugging(debug);await waitFor(()=>!vscode.debug.activeDebugSession,'first session stops');
  const after=await launch();assert.equal(after.view.entities[1].position[2],2.5);
  assert.deepEqual(after.view.links.map(link=>[link.id,link.source,link.target]),before.view.links.map(link=>[link.id,link.source,link.target]));
  const neighbors=await debug.customRequest('pixelFlowSpace',{...after.args,path:after.input.path,neighbors:{origin:after.view.entities[0].id,radius:2}});assert.equal(neighbors.total,0);
  await debug.customRequest('setBreakpoints',{source:{path:uri.fsPath},breakpoints:[]});
  await debug.customRequest('continue',{threadId:1});await waitFor(()=>outputs.length===4,'recovered edited program completes');
  assert.deepEqual(outputs,[0,0,9,3]);
  console.log('PASS: PNG-recovered native source, native language providers, graph projection, DAP link payload, coordinate origin/edit and restarted execution.');
 }finally{if(debug)await vscode.debug.stopDebugging(debug);tracker.dispose();panel.dispose();}
}
module.exports={run};
