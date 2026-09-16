// Headless bridge/backend integration; this is separate from VS Code UI acceptance.
const assert = require('node:assert/strict');
const path = require('node:path');
const { once } = require('node:events');
const { Bridge, encodeWire, decodeWire } = require('../src/bridge');
const root = path.resolve(__dirname, '../..');
async function main() {
  const literal = { image: Buffer.from([0,255]), values:[{$bytes:'literal'},{$escaped:'literal'}] };
  assert.deepEqual(decodeWire(JSON.parse(JSON.stringify(encodeWire(literal)))),literal);
  const logs=[];
  const bridge=new Bridge(process.env.PIXELLANG_PYTHON || path.join(root,'.venv/bin/python'),root,
                          {append:v=>logs.push(v),appendLine:v=>logs.push(v)});
  const deadline=setTimeout(()=>{bridge.dispose();process.exitCode=1;},60000);
  try {
    const project={files:{'main.pxl':'fn main(){}'},entry:'main.pxl',revision:1};
    const first=await bridge.call('check',project);
    assert.equal(first.ok,true);assert.match(first.compilerSha256,/^[a-f0-9]{64}$/);
    const warm=await bridge.call('check',{...project,revision:2});
    assert.ok(warm.metrics.parseHits>0);
    const controller=new AbortController();
    const slow={files:{'main.pxl':Array.from({length:2000},(_,i)=>`fn f${i}()->int=${i}`).join('\n')+'\nfn main(){}'},revision:3};
    const pending=bridge.call('check',slow,{signal:controller.signal});
    setTimeout(()=>controller.abort(),100);
    await assert.rejects(pending,{name:'AbortError'});
    assert.equal((await bridge.call('check',{...project,revision:4})).ok,true);
    const built=await bridge.call('build',{files:{'main.pxl':'fn main() {\nlet answer = 42\nprint(answer)\n}'},entry:'main.pxl'});
    assert.equal(built.inspection.ir,null);
    const inspected=await bridge.call("inspect-ir",{session:built.session});
    assert.ok(inspected.ir.functions);
    assert.equal(Buffer.from(built.image,'base64').subarray(0,8).toString('hex'),'89504e470d0a1a0a');
    const recovered=await bridge.call('recover',{image:built.image});
    assert.equal(recovered.entry,'main.pxl');
    assert.match(recovered.files['main.pxl'],/let answer/);
    const paused=await bridge.call('continue',{session:built.session,breakpoints:{'main.pxl':[3]}});
    assert.equal(paused.location.source,'main.pxl');
    assert.equal(paused.location.start.line,3);
    assert.deepEqual(paused.output,[]);
    const finished=await bridge.call('run',{session:built.session});
    assert.deepEqual(finished.output,['42']);
    const document=await bridge.call('recording',{session:built.session});
    assert.equal(document.backend,'selfhost');
    const restored=await bridge.call('restore',{document});
    assert.deepEqual(restored.output,['42']);
    const running=await bridge.call('build',{files:{'main.pxl':'fn main(){while true {let value=1}}'}});
    await bridge.call('run',{session:running.session});
    const stopped=await bridge.call('cancel',{session:running.session});
    assert.equal(stopped.state.halted,true);
    assert.match(stopped.state.error.message,/cancelled/);
    assert.equal(bridge.pending.size,0);
    console.log('Node Bridge / Studio service: cache, cancellation, native build, breakpoint, execution, replay and codec passed.');
  } finally {
    clearTimeout(deadline);
    const exited=once(bridge.process,'exit');
    bridge.dispose();
    await exited;
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
