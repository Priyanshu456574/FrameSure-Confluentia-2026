"""Isolated localhost HTTP proxy faults; never changes host network settings."""
import hashlib
import http.client
import json
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from backend.api import create_server
from scripts.benchmark import json_req

ROOT=Path(__file__).resolve().parent.parent

class Proxy(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0'
    def log_message(self,*args): pass
    def do_GET(self): self.forward()
    def do_POST(self): self.forward()
    def do_PUT(self): self.forward()
    def forward(self):
        profile=self.server.profile
        is_chunk=self.command=='PUT'
        size=int(self.headers.get('Content-Length','0'))
        if is_chunk and profile.get('disconnect_once') and not self.server.injected:
            self.server.injected=True
            self.rfile.read(min(size,8192))
            self.close_connection=True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        blocks=[]
        remaining=size
        while remaining:
            data=self.rfile.read(min(16384,remaining))
            if not data:break
            blocks.append(data);remaining-=len(data)
            if is_chunk and profile.get('bytes_per_second'):
                time.sleep(len(data)/profile['bytes_per_second'])
        body=b''.join(blocks)
        if is_chunk:time.sleep(profile.get('latency_seconds',0))
        if is_chunk and profile.get('http503_once') and not self.server.injected:
            self.server.injected=True
            data=b'{"error":"isolated proxy injected HTTP 503"}'
            self.send_response(503);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        conn=http.client.HTTPConnection('127.0.0.1',self.server.backend_port,timeout=120)
        try:
            headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection')}
            conn.request(self.command,self.path,body=body,headers=headers)
            response=conn.getresponse();data=response.read()
            self.send_response(response.status);self.send_header('Content-Length',str(len(data)))
            self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data)
        finally:conn.close()

def run():
    data=(ROOT/'samples/valid_30s_30fps.mp4').read_bytes();sha=hashlib.sha256(data).hexdigest()
    profiles=[('stable',{}),('latency',{'latency_seconds':.075}),
              ('throttled',{'bytes_per_second':524288}),('TCP interruption',{'disconnect_once':True}),
              ('transient request failure',{'http503_once':True})]
    results=[]
    with tempfile.TemporaryDirectory() as td:
        backend=create_server('127.0.0.1',0,Path(td));worker=threading.Thread(target=backend.serve_forever,daemon=True);worker.start()
        try:
            for name,profile in profiles:
                proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
                proxy.backend_port=backend.server_port;proxy.profile=profile;proxy.injected=False
                thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start()
                started=time.perf_counter();retries=0;submitted=0
                try:
                    session=json_req(proxy.server_port,'POST','/api/uploads',dict(filename='valid.mp4',size=len(data),sha256=sha))
                    for i in range(session['chunk_count']):
                        part=data[i*session['chunk_size']:(i+1)*session['chunk_size']]
                        for attempt in range(3):
                            submitted+=len(part)
                            conn=http.client.HTTPConnection('127.0.0.1',proxy.server_port,timeout=10)
                            try:
                                conn.request('PUT',f"/api/uploads/{session['id']}/chunks/{i}",body=part,headers={'X-Chunk-SHA256':hashlib.sha256(part).hexdigest()})
                                r=conn.getresponse();reply=json.loads(r.read())
                                if r.status==503:raise ConnectionError('injected 503')
                                assert r.status in (200,201) and reply['durably_stored'],reply
                                break
                            except (ConnectionError,http.client.HTTPException,OSError):
                                retries+=1
                                if attempt==2:raise
                            finally:conn.close()
                    status=json_req(proxy.server_port,'POST',f"/api/uploads/{session['id']}/finalize")
                    report=json_req(proxy.server_port,'GET',f"/api/uploads/{session['id']}/report")
                    assert status['status']=='COMPLETED' and status['server_sha256']==sha
                    assert report['processing']['source']['source_frame_count']==900
                    row={'scenario':name,'parameters':profile,'status':'PASSED','retries':retries,
                         'elapsed_seconds':round(time.perf_counter()-started,3),'sha256_match':True,
                         'source_frames':900,'submitted_chunk_payload_bytes':submitted,'injection_triggered':proxy.injected}
                    results.append(row);print(json.dumps(row),flush=True)
                finally:proxy.shutdown();proxy.server_close();thread.join(5)
        finally:backend.shutdown();backend.server_close();worker.join(5)
    output={'category':'controlled userspace TCP/HTTP proxy impairment on loopback',
            'host_network_configuration_changed':False,'physical_network_tested':False,
            'packet_loss_tested':False,'video_decoder':'real FFmpeg','results':results,
            'limitations':['HTTP failure and TCP connection close are not IP packet loss.',
                           'Rate limiting and latency are proxy scheduling; not a kernel network emulator.',
                           'No physical Wi-Fi/mobile switching or physical signal percentage was measured.']}
    out=ROOT/'evidence/network';out.mkdir(parents=True,exist_ok=True)
    (out/'controlled_results.json').write_text(json.dumps(output,indent=2)+'\n')

if __name__=='__main__':run()
