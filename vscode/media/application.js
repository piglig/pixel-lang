(function(root) {
  function spacesIn(value, path='output', result=[]) {
    if (!value || typeof value !== 'object') return result;
    if (typeof value.identity === 'string' && Array.isArray(value.entities) && Array.isArray(value.links)) {
      result.push({path, value}); return result;
    }
    for (const [key,item] of Object.entries(value)) spacesIn(item,path+'.'+key,result);
    return result;
  }
  function sceneView(space, time) {
    return {identity:space.identity, version:space.version, unit:'native coordinates',time,
      start:0,total:space.entities.length,
      entities:space.entities.map(e=>({id:e.identity,position:[e.position.x,e.position.y,e.position.z],payload:e.payload})),
      links:space.links.map(e=>({id:e.identity,source:e.source.identity??e.source,target:e.target.identity??e.target}))};
  }
  class Playback {
    constructor(send, schedule=(fn, delay)=>setTimeout(fn, delay), clear=id=>clearTimeout(id)) {
      this.send=send; this.schedule=schedule; this.clear=clear; this.timer=null;
      this.running=false; this.busy=false; this.state=null; this.speed=1;
    }
    pause() { this.running=false; this.clear(this.timer); this.timer=null; }
    command(message) {
      if (this.busy) return false;
      this.clear(this.timer); this.timer=null; this.busy=true; this.send(message); return true;
    }
    canAdvance() {
      return Boolean(this.state && (this.state.cursor < this.state.length || this.state.timed !== false || this.state.pending?.length));
    }
    step() {
      if (!this.canAdvance()) return false;
      return this.command(this.state.cursor < this.state.length ? {type:'seek',index:this.state.cursor+1} : {type:'step'});
    }
    play() { if (!this.canAdvance()) return; this.running=true; if (!this.busy) this.step(); }
    receive(state) {
      this.state=state; this.busy=false;
      if (!this.canAdvance()) this.pause();
      if (this.running) {
        this.clear(this.timer);
        this.timer=this.schedule(()=>{this.timer=null;if(this.running)this.step();},Math.max(16,Number(state.interval_ns)/1000000/this.speed));
      }
    }
    fail() { this.pause(); this.busy=false; }
  }
  const exported={spacesIn,sceneView,Playback};
  if (typeof module !== 'undefined') { module.exports=exported; return; }
  const vscode=acquireVsCodeApi(), $=id=>document.getElementById(id);
  const player=new Playback(message=>{ $('error').hidden=true; vscode.postMessage(message); updateControls(); });
  const renderer=new root.PixelNativeSpace.NativeSpaceRenderer($('scene'));
  let state=null, spaces=[], framing=null, sceneIdentity=null, drag=null;
  const pause=()=>{player.pause();updateControls();};
  function updateControls() {
    const busy=player.busy, live=state && state.cursor===state.length;
    for (const id of ['step','latest','time','save','load']) $(id).disabled=busy || !state;
    for (const id of ['save','load']) $(id).hidden=Boolean(state?.journal);
    $('debug').disabled=busy || !state || !state.cursor || player.running;
    $('step').disabled=busy || !player.canAdvance();
    $('play').disabled=busy || !player.canAdvance() || player.running;
    $('pause').disabled=!player.running;
    $('send').disabled=busy || !live;
    for (const button of $('pending').querySelectorAll('button')) button.disabled=busy || !live || player.running;
  }
  function draw() {
    const selected=spaces.find(item=>item.path===$('spaces').value);
    $('empty').hidden=Boolean(selected);
    $('spaces').disabled=!selected;
    $('fit').disabled=!selected;
    if (!selected) { const canvas=$('scene');canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);return; }
    const view=sceneView(selected.value,state.tick??'0');
    if (sceneIdentity!==view.identity) {
      sceneIdentity=view.identity;
      // Hold camera framing fixed across frames so movement remains visible.
      framing={entities:[{position:[-10,-10,-10]},{position:[10,10,10]}]};
    }
    renderer.draw(view,null,framing,true);
  }
  function variantChanged() {
    const fields=state?.variants[$('variant').value]||{};
    const defaults=Object.fromEntries(Object.entries(fields).map(([name,type])=>[name,type==='string'?'':type==='bool'?false:type==='int'||type==='float64'?0:null]));
    $('payload').value=JSON.stringify(defaults,null,2);
    $('schema').textContent=JSON.stringify({fields,records:state.records,enums:state.enums},null,2);
  }
  function render(value) {
    state=value;player.receive(value);
    $('time').max=String(value.length);$('time').value=String(value.cursor);
    $('position').textContent=`${value.cursor} / ${value.length}`;
    $('status').textContent=`${value.timed===false?'Events':`Tick ${value.tick??'—'} · ${value.elapsed_ns} ns`} · ${value.cursor===value.length?'Live':'History'}${value.journal?' · Saved to journal':''}`;
    $('output').textContent=value.viewText;$('state').textContent=value.stateText;
    const oldVariant=$('variant').value, variants=Object.keys(value.variants);
    if (JSON.stringify(Array.from($('variant').options,o=>o.value))!==JSON.stringify(variants)) {
      $('variant').replaceChildren(...variants.map(name=>new Option(name,name)));
      if (variants.includes(oldVariant)) $('variant').value=oldVariant;
      variantChanged();
    }
    $('pending').replaceChildren(...value.pending.map(input=>{
      const row=document.createElement('div'),text=document.createElement('pre'),button=document.createElement('button');
      text.textContent=`${value.timed===false?'Event '+input.sequence:'Tick '+input.tick}\n${input.valueText}`;button.textContent='Discard';
      button.onclick=()=>{pause();player.command({type:'discard',sequence:input.sequence});};row.append(text,button);return row;
    }));
    const oldSpace=$('spaces').value; spaces=spacesIn(value.view);
    $('spaces').replaceChildren(...spaces.map(item=>new Option(item.path,item.path)));
    if (spaces.some(item=>item.path===oldSpace)) $('spaces').value=oldSpace;
    $('empty').textContent=value.cursor?'Output has no spatial scene. Inspect its values on the right.':'Step to inspect the initial output.';
    updateControls();draw();
  }
  $('play').onclick=()=>{player.play();updateControls();};$('pause').onclick=pause;
  $('step').onclick=()=>{pause();player.step();};
  $('latest').onclick=()=>{pause();player.command({type:'seek',index:state.length});};
  $('time').onchange=()=>{pause();player.command({type:'seek',index:Number($('time').value)});};
  $('speed').onchange=()=>{player.speed=Number($('speed').value);};
  $('variant').onchange=variantChanged;
  $('send').onclick=()=>{player.command({type:'input',valueText:`{"variant":${JSON.stringify($('variant').value)},"fields":${$('payload').value}}`});};
  for (const type of ['save','load','debug']) $(type).onclick=()=>{pause();player.command({type});};
  $('spaces').onchange=()=>{sceneIdentity=null;draw();};
  $('fit').onclick=()=>{const selected=spaces.find(item=>item.path===$('spaces').value);if(selected){framing=sceneView(selected.value,state.tick);draw();}};
  $('scene').onpointerdown=event=>{drag={x:event.clientX,y:event.clientY};$('scene').setPointerCapture(event.pointerId);};
  $('scene').onpointermove=event=>{if(!drag)return;renderer.yaw+=(event.clientX-drag.x)*.008;renderer.pitch=Math.max(-1.5,Math.min(1.5,renderer.pitch+(event.clientY-drag.y)*.008));drag={x:event.clientX,y:event.clientY};draw();};
  $('scene').onpointerup=$('scene').onpointercancel=()=>{drag=null;};
  $('scene').addEventListener('wheel',event=>{event.preventDefault();renderer.zoom=Math.max(.1,Math.min(10,renderer.zoom*Math.exp(-event.deltaY*.001)));draw();},{passive:false});
  new ResizeObserver(draw).observe($('scene'));
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});
  window.addEventListener('message',event=>{
    const message=event.data;
    if(message.type==='state')render(message.value);
    if(message.type==='pause')pause();
    if(message.type==='error'){player.fail();$('error').textContent=message.message;$('error').hidden=false;updateControls();}
  });
  updateControls();vscode.postMessage({type:'ready'});
})(globalThis);
