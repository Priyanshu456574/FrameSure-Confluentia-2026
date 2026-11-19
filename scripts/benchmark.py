"""Run actual local HTTP uploads; disconnect TCP sockets at deterministic payload offset.

Transport test is real HTTP/TCP on loopback; the failure and signal-to-delay
mapping are *emulated*, not a claim about wireless radio or physical packet loss.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import tempfile
import threading
import time
from pathlib import Path

from backend.api import create_server

ROOT=Path(__file__).resolve().parent.parent
VIDEO=ROOT/'samples'/'valid_30s_30fps.mp4'
BLOCK=16*1024


def hexdigest(data): return hashlib.sha256(data).hexdigest()


class Wire:
    def __init__(self,port, seed=2026, cutoff=None, pattern=(100,80,45,20,65,95)):
        self.port=port;self.cutoff=cutoff;self.triggered=False
        self.wire_bytes=0;self.attempts=0;self.recovery_events=0
        self.pattern=pattern;self.signal_samples=[];self.seed=seed

    def simulated_signal(self):
        # Choose deterministic signal level from real bytes sent. The numbers
        # affect only a small repeatable application-layer delay in this harness.
        level=self.pattern[(self.wire_bytes//(128*1024))%len(self.pattern)]
        self.signal_samples.append(level)
        return level

    def upload(self,path,data,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=30)
        self.attempts+=1
        try:
            conn.putrequest('POST' if path=='/api/legacy-upload' else 'PUT',path)
            conn.putheader('Content-Length',str(len(data)))
            for k,v in (headers or {}).items():conn.putheader(k,v)
            conn.endheaders()
            for offset in range(0,len(data),BLOCK):
                piece=data[offset:offset+BLOCK]
                if self.cutoff is not None and not self.triggered and self.wire_bytes >= self.cutoff:
                    self.triggered=True;self.recovery_events+=1
                    conn.close()
                    return False,None
                signal=self.simulated_signal()
                # Adjustable, repeatable delay. Not a physical throughput cap.
                time.sleep((100-signal)*0.000008)
                conn.send(piece)
                self.wire_bytes+=len(piece)
            resp=conn.getresponse();raw=resp.read();result=json.loads(raw)
            if resp.status>=400:raise RuntimeError(f'HTTP {resp.status}: {result}')
            return True,result
        finally:conn.close()


def json_req(port,method,path,obj=None):
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=120)
    data=json.dumps(obj).encode() if obj is not None else None
    headers={'Content-Type':'application/json'} if data else {}
    try:
        conn.request(method,path,body=data,headers=headers)
        r=conn.getresponse();out=json.loads(r.read())
        if r.status>=400:raise RuntimeError(f'HTTP {r.status}: {out}')
        return out
    finally:conn.close()


def legacy_run(port,video,cutoff):
    w=Wire(port,cutoff=cutoff)
    finished=False;last=None
    for tries in range(5):
        ok,last=w.upload('/api/legacy-upload',video,{'X-File-SHA256':hexdigest(video)})
        if ok:finished=True;break
    return {'architecture':'single-request','success':finished,'wire_payload_bytes':w.wire_bytes,
            'retry_count':w.attempts-1,'recovery_events':w.recovery_events,
            'sha256_match':bool(last and last['sha256']==hexdigest(video)),
            'source_frames':last['source_frame_count'] if last else None}


def chunked_run(port,video,cutoff):
    w=Wire(port,cutoff=cutoff)
    start=json_req(port,'POST','/api/uploads',dict(filename='valid_30s_30fps.mp4',size=len(video),sha256=hexdigest(video)))
    for i in range(start['chunk_count']):
        part=video[i*start['chunk_size']:(i+1)*start['chunk_size']]
        for tries in range(5):
            ok,ans=w.upload(f"/api/uploads/{start['id']}/chunks/{i}",part,{'X-Chunk-SHA256':hexdigest(part)})
            if ok:break
        else:raise RuntimeError('Resumable upload exceeded retry limit')
    completed=json_req(port,'POST',f"/api/uploads/{start['id']}/finalize")
    return {'architecture':'resumable-chunks','success':completed['status']=='COMPLETED',
            'wire_payload_bytes':w.wire_bytes,'retry_count':w.attempts-start['chunk_count'],
            'recovery_events':w.recovery_events,
            'sha256_match':bool(completed['server_sha256']==hexdigest(video)),
            'source_frames':json_req(port,'GET',f"/api/uploads/{start['id']}/report")['processing']['source']['source_frame_count']}


def main(output:Path):
    if not VIDEO.exists():
        from scripts.make_samples import generate
        generate(ROOT/'samples')
    video=VIDEO.read_bytes();rows=[]
    with tempfile.TemporaryDirectory() as td:
        server=create_server('127.0.0.1',0,Path(td),enable_legacy_benchmark=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for condition,cutoff in [('stable',None),('injected_tcp_disconnect_at_~50pct',len(video)//2)]:
                for architecture,func in [('baseline',legacy_run),('resumable',chunked_run)]:
                    begun=time.monotonic()
                    row=func(server.server_port,video,cutoff)
                    row.update({'scenario':condition,'elapsed_wall_seconds':round(time.monotonic()-begun,3),
                                'source_bytes':len(video),'payload_overhead_pct':round(100*(row['wire_payload_bytes']/len(video)-1),2),
                                'signal_percent_pattern':[100,80,45,20,65,95]})
                    rows.append(row)
                    print(json.dumps(row,indent=2))
        finally:server.shutdown();server.server_close();thread.join(timeout=10)
    result={'run_context':'Actual local HTTP/TCP byte transfers; one deterministic client-side socket disconnect in disrupted condition; synthetic signal values influence small per-block delay only',
            'date_utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            'trial_count':len(rows),'trials':rows,
            'limitations':['Not a physical-radio, packet-loss, browser-switching or large-scale load benchmark.',
                           'One deterministic interruption per disturbed trial, not statistical reliability rates.',
                           'Same file and corruption/validation gate on both HTTP upload styles.',
                           'Server loopback timing is not representative of real networks.']}
    output.parent.mkdir(exist_ok=True,parents=True)
    output.write_text(json.dumps(result,indent=2))
    print('\nReport:',output)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'evidence'/'http_benchmark.json')
    main(p.parse_args().out)
