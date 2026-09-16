const assert=require('node:assert/strict');
const {PixelDebug}=require('../src/debug');
class Emitter { constructor(){this.messages=[];this.event=()=>({dispose(){}});} fire(v){this.messages.push(v);} dispose(){} }
async function main(){
  let files={'main.pxl':'fn main(){}'},published=0,release,started;
  const entered=new Promise(resolve=>{started=resolve;});
  const host={collect:async()=>({entry:'main.pxl',root:'/tmp/project',files:{...files}}),show(){},publish(){published++;},
    service:()=>({call:(_,args,options)=>new Promise((resolve,reject)=>{
      release=resolve;started();
      if(options.signal.aborted) reject(new Error('cancelled'));
      else options.signal.addEventListener('abort',()=>reject(new Error('cancelled')),{once:true});
    })})};
  const debug=new PixelDebug({EventEmitter:Emitter,Uri:{file:fsPath=>({fsPath})}},host);
  const launch=debug.handleMessage({seq:1,command:'launch',arguments:{program:'/tmp/project/main.pxl'}});
  await entered;
  await debug.handleMessage({seq:2,command:'terminate'});
  await launch;
  assert.equal(published,0);
  assert.equal(debug.emitter.messages.find(m=>m.request_seq===1).success,false);
  host.service=()=>({call:async()=>{files={'main.pxl':'fn main(){print(1)}'};return {state:{halted:false}};}});
  await debug.handleMessage({seq:3,command:'launch',arguments:{program:'/tmp/project/main.pxl'}});
  assert.equal(published,0);
  assert.equal(debug.emitter.messages.find(m=>m.request_seq===3).success,false);
  host.service=()=>({call:async()=>({state:{halted:false}})});
  await debug.handleMessage({seq:4,command:'launch',arguments:{program:'/tmp/project/main.pxl'}});
  assert.equal(published,1);
  assert.equal(debug.emitter.messages.find(m=>m.request_seq===4).success,true);
  debug.dispose();
  console.log('DAP launch cancellation, stale-source rejection and valid launch passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
