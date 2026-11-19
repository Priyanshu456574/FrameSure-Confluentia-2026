// DOM doubles exercise state ownership. This is not a browser compatibility test.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const source = readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
new vm.Script(source);
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {disabled:false,textContent:'',files:[],style:{},
    classList:{toggle(){}},addEventListener(){},prepend(){},children:[],replaceChildren(){}});
  return elements.get(id);
};
const context = vm.createContext({document:{getElementById:element,createElement:()=>element('created')},
  window:{addEventListener(){}},localStorage:{getItem:()=>null,setItem(){}},
  performance:{now:()=>0},console,alert(){},setTimeout,clearTimeout,
  crypto:{subtle:{digest:async()=>new Uint8Array([1,2]).buffer}}});
vm.runInContext(source,context);
const run = code => vm.runInContext(code,context);
const outcomes=[];
run('setControls(true)'); assert.equal(element('fileInput').disabled,true);
outcomes.push('PASS: upload locks file selection');
run('running=true; file={name:"original"};');
element('fileInput').files=[{name:'replacement',size:5}];
await run('onFile()'); assert.equal(run('file.name'),'original');
outcomes.push('PASS: running upload ignores file-selection event');
run('running=false; finalizedReport={old:true};');
element('verified').textContent='YES'; element('download').disabled=false;
element('fileInput').files=[]; await run('onFile()');
assert.equal(run('finalizedReport'),null); assert.equal(element('download').disabled,true);
assert.equal(element('verified').textContent,'Pending');
outcomes.push('PASS: selection clears stale report and verification');
let resolveOld;
element('fileInput').files=[{name:'old',size:1,arrayBuffer:()=>new Promise(r=>{resolveOld=r;})}];
const pending = run('onFile()');
element('fileInput').files=[]; await run('onFile()');
resolveOld(new ArrayBuffer(1)); await pending;
assert.equal(run('fileHash'),null);
outcomes.push('PASS: stale asynchronous hash cannot replace current selection');
// Exercise the actual client recovery logic with explicit API doubles.
let phase='timeout';
const active={id:'test-session',file_sha256:'01',file_size:1,chunk_count:1,chunk_size:1,
  received:[{index:0,size:1}],transfer_verified:true,server_sha256:'01',status:'VERIFYING',
  error:'Processing timed out; retry processing'};
const report={processing:{source:{source_frame_count:900},target_30fps:{frame_count:900},
  anomaly_count:0,anomalies:[]}};
context.fetch=async(url,options={})=>{
  const isFinalize=url.endsWith('/finalize');
  const data=url.endsWith('/report')?report:(isFinalize && phase==='timeout'?{error:active.error}:
    {...active,status:phase==='timeout'?'VERIFYING':'COMPLETED'});
  const status=isFinalize && phase==='timeout'?503:200;
  return {ok:status===200,status,text:async()=>JSON.stringify(data)};
};
context.navigator={onLine:true};
run('file={name:"valid.mp4",size:1}; fileHash="01"; session='+JSON.stringify(active));
await run('begin()');
assert.equal(element('stateLabel').textContent,'VERIFYING');
assert.equal(element('start').textContent,'Retry processing');
assert.equal(element('download').disabled,true);
assert.equal(element('fileInput').disabled,false);
outcomes.push('PASS: HTTP 503 leaves retryable UI without a false report');
phase='success';await run('begin()');
assert.equal(element('stateLabel').textContent,'COMPLETED');
assert.equal(element('sourceFrames').textContent,900);
assert.equal(element('download').disabled,false);
assert.equal(element('progressText').textContent,'100.0%');
outcomes.push('PASS: retry renders completion, frame counts and durable progress');
element('chaos').checked=true;element('strength').value='20';
assert.ok(run('plannedDelayMs(262144)')>1000);
run('Math.random=()=>0');assert.equal(run('shouldFailOneRequest()'),true);
run('Math.random=()=>1');assert.equal(run('shouldFailOneRequest()'),false);
run('artificialOutageUntil=Date.now()+30');
const gateStart=Date.now();await run('respectPause()');
assert.ok(Date.now()-gateStart>=25);
outcomes.push('PASS: emulator delay, request-failure branches and outage gate');
outcomes.push('PASS: frontend JavaScript parsed');
writeFileSync(new URL('../evidence/frontend_results.log',import.meta.url),outcomes.join('\n')+'\n');
console.log(outcomes.join('\n'));
