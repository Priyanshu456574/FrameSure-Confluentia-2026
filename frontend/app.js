/* FrameSure: same-origin, browser-native transfer with honest app-layer chaos emulation. */
const $ = (id) => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value; };
const store = 'framesure-active-v1';
let file = null, fileHash = null, session = null, paused = false, running = false;
let artificialOutageUntil = 0, retries = 0, recoveries = 0, acknowledgedThisRunBytes = 0;
let startedAt = 0, desiredConcurrency = 1, successWindow = [], finalizedReport = null;
let selectionVersion = 0;
const MAX_RETRIES = 7;
const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
const byteSize = (n) => n > 1048576 ? (n / 1048576).toFixed(2) + ' MiB' : (n / 1024).toFixed(1) + ' KiB';
const asHex = (arr) => Array.from(new Uint8Array(arr)).map((b) => b.toString(16).padStart(2, '0')).join('');
const hash = async (bytes) => asHex(await crypto.subtle.digest('SHA-256', bytes));
const signalStrength = () => +$('strength').value;
function log(message) {
  const d = document.createElement('div'); d.textContent = `${new Date().toLocaleTimeString()}  ${message}`;
  $('events').prepend(d);
  if ($('events').children.length > 110) $('events').lastChild.remove();
}
function state(name, detail) {
  text('stateLabel', name); text('detail', detail || '');
  $('dot').classList.toggle('active', ['UPLOADING','COMPLETED','VERIFYING'].includes(name));
}
async function api(url, options = {}) {
  const r = await fetch(url, options);
  const raw = await r.text();
  let data;
  try { data = JSON.parse(raw); } catch { throw new Error(`Non-JSON HTTP ${r.status} response`); }
  if (!r.ok) throw Object.assign(new Error(data.error || `HTTP ${r.status}`), { status: r.status, result: data });
  return data;
}
function updateProgress() {
  if (!session) return;
  const bytes = session.received.reduce((a, b) => a + b.size, 0);
  const frac = bytes / session.file_size;
  $('bar').style.width = (100 * frac).toFixed(2) + '%';
  text('progressText', (100 * frac).toFixed(1) + '%');
  text('transferred', `${byteSize(bytes)} / ${byteSize(session.file_size)}`);
  text('retries', retries); text('recovered', recoveries);
  text('workers', desiredConcurrency); text('throughput', startedAt ? byteSize(acknowledgedThisRunBytes / Math.max(.2,(performance.now()-startedAt)/1000)) + '/s' : '—');
}
function setControls(uploading = false) {
  $('fileInput').disabled = uploading;
  $('start').disabled = !file || !fileHash || uploading;
  $('pause').disabled = !uploading;
  $('disconnect').disabled = !uploading;
  $('pause').textContent = paused ? 'Resume' : 'Pause';
  $('start').textContent = session?.status === 'VERIFYING' ? 'Retry processing' : 'Start verified upload';
}
function persist() {
  if (!session) return;
  localStorage.setItem(store, JSON.stringify({id:session.id,sha256:session.file_sha256,size:session.file_size}));
}
async function onFile() {
  if (running) return;
  const version = ++selectionVersion;
  file = $('fileInput').files[0] || null; fileHash=null;
  session = null; finalizedReport = null;
  $('download').disabled = true;
  for (const id of ['hashValue','serverHash','sourceFrames','targetFrames','anomalyCount']) text(id,'—');
  text('verified','Pending'); text('videoValid','Pending');
  text('timeline','After verification, frame anomaly evidence will appear here.');
  text('progressText','0%'); $('bar').style.width='0%';
  text('transferred','0 B'); text('fileInfo','No file selected');
  retries=0; recoveries=0; successWindow=[]; acknowledgedThisRunBytes=0; startedAt=0;
  setControls(false);
  if (!file) { state('READY','Awaiting file'); return; }
  if (!file.size || file.size > 100 * 1024 * 1024) {
    alert('File size must be between 1 byte and 100 MiB'); file=null; return;
  }
  state('HASHING','Computing SHA-256, before any upload');
  text('fileInfo', `${file.name} • ${byteSize(file.size)}`); setControls(false);
  try {
    // WebCrypto is not streaming: maximum 100MiB blob and memory cost are documented.
    const selectedHash = await hash(await file.arrayBuffer());
    if (version !== selectionVersion) return;
    fileHash = selectedHash;
    text('hashValue', fileHash); log(`Client SHA-256 ${fileHash}`);
    const saved = JSON.parse(localStorage.getItem(store) || 'null');
    if (saved && saved.sha256 === fileHash && saved.size === file.size) {
      try {
        const existing = await api(`/api/uploads/${saved.id}`);
        if (version !== selectionVersion) return;
        if (['CREATED','UPLOADING','PAUSED','VERIFYING','COMPLETED'].includes(existing.status)) {
          session = existing; persist(); updateProgress();
          log(`Recovered server-side session ${existing.id}, ${existing.received.length}/${existing.chunk_count} chunks acknowledged`);
          recoveries += 1;
          state('RECOVERED','Same-file SHA-256 matched; click Resume or Start');
          if (existing.status === 'COMPLETED') { await completed(version); setControls(false); }
          else setControls(false);
          return;
        }
      } catch(e) { log(`Saved session no longer active: ${e.message}`); }
    }
    session = null; state('READY','File hash ready; click Start verified upload'); setControls(false);
  } catch(e) {
    if (version !== selectionVersion) return;
    log(`Hash error: ${e.message}`); state('FAILED',e.message);
  }
}
async function getOrCreateSession() {
  if (session && session.file_sha256 === fileHash && session.file_size === file.size) {
    session = await api(`/api/uploads/${session.id}`);
    if (session.status === 'VERIFYING') { log('Re-entering crash-safe finalize'); return; }
    if (['REJECTED','FAILED'].includes(session.status)) session=null;
  }
  if (!session) {
    session = await api('/api/uploads',{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({filename:file.name,size:file.size,sha256:fileHash})});
    log(`Session created ${session.id}; ${session.chunk_count} chunks of up to ${byteSize(session.chunk_size)}`);
  }
  persist(); updateProgress();
}
async function respectPause() {
  while (paused || Date.now() < artificialOutageUntil || !navigator.onLine) {
    await sleep(200);
  }
}
function plannedDelayMs(size) {
  if (!$('chaos').checked) return 0;
  const strength = signalStrength();
  const throughputBps = (60 + 540 * strength / 100) * 1024;
  return Math.round(size / throughputBps * 1000 + (100 - strength) * 2.5);
}
function shouldFailOneRequest() {
  if (!$('chaos').checked) return false;
  return Math.random() < (0.19 * (100 - signalStrength()) / 80);
}
async function transferOne(idx) {
  const chunk = file.slice(idx * session.chunk_size, Math.min((idx+1) * session.chunk_size, file.size));
  const data = await chunk.arrayBuffer();
  const chunkHash = await hash(data);
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    await respectPause();
    text('chunk', `${idx+1}/${session.chunk_count}`);
    const tick = performance.now();
    try {
      if ($('chaos').checked) {
        await sleep(plannedDelayMs(data.byteLength));
        await respectPause();
        if (shouldFailOneRequest()) throw new Error('Emulated transient request failure');
      }
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      let reply;
      try {
        reply = await api(`/api/uploads/${session.id}/chunks/${idx}`,
            {method:'PUT',headers:{'X-Chunk-SHA256':chunkHash,'Content-Type':'application/octet-stream'},
             body:data,signal:controller.signal});
      } finally { clearTimeout(timeout); }
      if (reply.sha256 !== chunkHash || !reply.durably_stored) throw new Error('Missing durable server acknowledgment');
      acknowledgedThisRunBytes += data.byteLength;
      if (!session.received.some(piece=>piece.index===idx)) session.received.push({index:idx,size:data.byteLength,sha256:chunkHash});
      updateProgress();
      successWindow.push({ok:true,ms:performance.now()-tick});
      if (successWindow.length>12) successWindow.shift();
      if (reply.duplicate) log(`Chunk ${idx+1}: idempotent duplicate acknowledged`);
      return {index:idx,size:data.byteLength,sha256:chunkHash};
    } catch(e) {
      if (e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429) throw e;
      retries++;
      successWindow.push({ok:false,ms:performance.now()-tick});
      if (successWindow.length>12) successWindow.shift();
      updateProgress();
      if (attempt === MAX_RETRIES) throw new Error(`Chunk ${idx+1} failed after ${MAX_RETRIES+1} attempts: ${e.message}`);
      const jitter = Math.random() * 250;
      const delay = Math.min(4000, 250 * 2 ** attempt) + jitter;
      log(`Chunk ${idx+1}: ${e.message}; retry ${attempt+1}/${MAX_RETRIES} in ${Math.round(delay)}ms`);
      await sleep(delay);
      recoveries++;
    }
  }
}
function adaptWorkers() {
  if (!$('chaos').checked) { desiredConcurrency=3; return; }
  const recent = successWindow.slice(-8), failures = recent.filter(x=>!x.ok).length;
  const meanLatency = recent.length ? recent.reduce((a,x)=>a+x.ms,0)/recent.length : 500;
  if (failures >= 2 || meanLatency > 5000 || signalStrength() <= 35) desiredConcurrency=1;
  else if (failures === 0 && meanLatency < 2500 && signalStrength() >= 75) desiredConcurrency=3;
  else desiredConcurrency=2;
}
async function begin() {
  if (!file || !fileHash || running) return;
  finalizedReport=null; $('download').disabled=true;
  for (const id of ['serverHash','sourceFrames','targetFrames','anomalyCount']) text(id,'—');
  text('verified','Pending'); text('videoValid','Pending');
  text('timeline','Awaiting this processing attempt.');
  paused = false; running = true; startedAt=performance.now(); acknowledgedThisRunBytes=0;
  setControls(true);
  try {
    await getOrCreateSession();
    state('UPLOADING','Sending SHA-256 verified chunks');
    if (session.status !== 'VERIFYING') {
      const received = new Map(session.received.map(x=>[x.index,x]));
      const backlog = Array.from({length:session.chunk_count},(_,i)=>i).filter(i=>!received.has(i));
      if (received.size) log(`Resuming from ${received.size} durably stored chunks; no full-file retransmission`);
      for (let start=0; start < backlog.length;) {
        await respectPause(); adaptWorkers(); updateProgress();
        const next = backlog.slice(start,start+desiredConcurrency); start += next.length;
        // All workers must settle before controls permit another upload.
        const results = await Promise.allSettled(next.map(transferOne));
        const failed = results.find(result=>result.status==='rejected');
        if (failed) throw failed.reason;
        session.received.sort((a,b)=>a.index-b.index);
        updateProgress();
      }
    }
    $('pause').disabled=true; $('disconnect').disabled=true;
    state('VERIFYING','Server assembling, hashing and decoding'); log('All chunks acknowledged; server-side finalization starting');
    try { session = await api(`/api/uploads/${session.id}/finalize`,{method:'POST'}); }
    catch(e) {
      if (e.status === 422 || e.status === 503) session = await api(`/api/uploads/${session.id}`);
      else throw e;
    }
    if (session.status === 'COMPLETED') await completed();
    else {
      state(session.status,session.error||'Validation incomplete');
      log(`Final status: ${session.status}: ${session.error}`);
      if (session.status==='REJECTED' || session.status==='FAILED') {
        finalizedReport=await api(`/api/uploads/${session.id}/report`);
        text('verified',session.transfer_verified?'YES — byte-perfect':'NO — not verified');
        text('serverHash',session.server_sha256||'—');
        text('videoValid',session.status==='REJECTED'?'REJECTED':'UNKNOWN — processing failed');
        $('download').disabled=false;
        text('anomalyCount','N/A');
        $('timeline').textContent=finalizedReport.error||'Rejected input';
      } else if(session.status==='VERIFYING') {
        text('verified',session.transfer_verified?'YES — matching SHA-256':'Pending');
        text('serverHash',session.server_sha256||'—');
        text('videoValid','PENDING — retry processing');
        text('detail',session.error||'Processing delayed. Retry processing; uploaded data is retained.');
      }
    }
  } catch(e) {
    log(`Interrupted: ${e.message}`); state('PAUSED',`Not accepted as complete: ${e.message}`);
    if(session) { try { session=await api(`/api/uploads/${session.id}`); updateProgress(); } catch {} }
  } finally {
    running=false; setControls(false);
  }
}
async function completed(expectedVersion=selectionVersion) {
  const id=session.id;
  const status=await api(`/api/uploads/${id}`);
  const report=await api(`/api/uploads/${id}/report`);
  if(expectedVersion!==selectionVersion)return;
  session=status; finalizedReport=report;
  state('COMPLETED','Full-file SHA-256 matched; video decoded successfully');
  text('serverHash',session.server_sha256);
  text('verified', 'YES — matching SHA-256');
  text('videoValid', 'YES — decoder passed');
  text('sourceFrames',finalizedReport.processing.source.source_frame_count);
  text('targetFrames',finalizedReport.processing.target_30fps.frame_count);
  text('anomalyCount',finalizedReport.processing.anomaly_count);
  $('download').disabled=false;
  const timeline=$('timeline'); timeline.replaceChildren();
  const anomalies=finalizedReport.processing.anomalies;
  if (!anomalies.length) timeline.textContent='No anomalies detected by available heuristics. This does NOT prove the source recording had no frame loss.';
  else for (const anomaly of anomalies.slice(0,80)) {
    const item=document.createElement('div');
    item.textContent=`${anomaly.timestamp_s ?? '?'}s · ${anomaly.type} · ${anomaly.confidence} (source frame ${anomaly.source_frame})`;
    timeline.append(item);
  }
  updateProgress(); log(`COMPLETED: ${finalizedReport.processing.source.source_frame_count} source frames, ${anomalies.length} heuristic anomalies`);
}
$('fileInput').addEventListener('change',onFile);
$('strength').addEventListener('input',()=>text('strengthValue',`${signalStrength()}%`));
$('start').addEventListener('click',begin);
$('pause').addEventListener('click',async()=>{
  paused=!paused;
  $('pause').textContent=paused?'Resume':'Pause';
  if (paused) {
    state('PAUSED','In-flight chunks may finish; acknowledged chunks are safe');
    if(session) await api(`/api/uploads/${session.id}/pause`,{method:'POST'}).catch(e=>log(e.message));
    log('Operator paused transfer');
  } else {state('UPLOADING','Resuming transfer');log('Operator resumed transfer');}
});
$('disconnect').addEventListener('click',()=>{
  artificialOutageUntil=Date.now()+5000;
  recoveries++;
  log('Emulated 5s outage: application-layer delay; in-flight requests may finish');
  state('PAUSED','Emulated outage; will auto-resume');
  setTimeout(()=>{if(!paused && running) state('UPLOADING','Recovered after emulated outage');},5100);
});
$('download').addEventListener('click',()=>{
  if(!finalizedReport)return;
  const blob=new Blob([JSON.stringify(finalizedReport,null,2)],{type:'application/json'});
  const anchor=document.createElement('a');anchor.href=URL.createObjectURL(blob);anchor.download=`framesure-${session.id}-report.json`;
  anchor.click();setTimeout(()=>URL.revokeObjectURL(anchor.href),1000);
});
window.addEventListener('offline',()=>log('Browser reported offline; transfer pauses and retries on reconnection'));
window.addEventListener('online',()=>log('Browser reported online; transfer can continue'));
log('Same-origin local app loaded. Upload session is durable on server; reselect same file after refresh to resume.');
