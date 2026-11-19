"""Regression and end-to-end validation. Run python -m unittest discover -s tests -v."""
from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import sys
import shutil
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from backend.api import create_server
from backend.service import CHUNK_SIZE, UploadError, Service
from backend.video import VideoValidationError, analyse_video

ROOT = Path(__file__).resolve().parent.parent
GOOD = ROOT / "samples" / "valid_30s_30fps.mp4"
BAD = ROOT / "samples" / "not_a_video.mp4"
GAP = ROOT / "samples" / "synthetic_timestamp_gap.mp4"
VIDEO_TOOLS = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
requires_video = unittest.skipUnless(VIDEO_TOOLS, 'FFmpeg/FFprobe unavailable; real video validation not executed')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dummy_video(path: Path):
    return {"video_valid": True, "source": {"source_frame_count": 900},
            "target_30fps": {"frame_count": 900}, "anomalies": [], "anomaly_count": 0,
            "limitations": ["simulated validator for fast deterministic service tests"]}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.svc = Service(Path(self.temp.name))
        self.data = b'A' * CHUNK_SIZE + b'B' * CHUNK_SIZE + b'C' * 17
        self.initial = self.svc.start('sample.mp4', len(self.data), digest(self.data))
        self.id = self.initial['id']

    def tearDown(self):
        self.temp.cleanup()

    def send_part(self, i, data=None):
        piece = data if data is not None else self.data[i*CHUNK_SIZE:(i+1)*CHUNK_SIZE]
        return self.svc.put(self.id, i, piece, digest(piece))

    def test_01_initial_state(self):
        self.assertEqual(self.initial['status'], 'CREATED')
        self.assertEqual(self.initial['chunk_count'], 3)
        self.assertFalse(self.initial['transfer_verified'])

    def test_02_out_of_order_chunks_valid(self):
        self.send_part(2); self.send_part(0); self.send_part(1)
        with patch('backend.service.analyse_video', side_effect=dummy_video):
            result = self.svc.finalize(self.id)
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual((Path(self.temp.name)/'final'/f'{self.id}.video').read_bytes(), self.data)
        self.assertTrue(self.svc.report(self.id)['transfer']['sha256_match'])

    def test_03_same_chunk_replay_is_idempotent(self):
        self.assertFalse(self.send_part(0)['duplicate'])
        self.assertTrue(self.send_part(0)['duplicate'])
        self.assertEqual(len(self.svc.status(self.id)['received']), 1)

    def test_04_conflicting_replay_rejected(self):
        self.send_part(0)
        altered = b'Z' * CHUNK_SIZE
        with self.assertRaises(UploadError) as context:
            self.send_part(0, altered)
        self.assertEqual(context.exception.status, 409)

    def test_05_bad_chunk_checksum_rejected(self):
        with self.assertRaises(UploadError) as context:
            self.svc.put(self.id, 0, b'A'*CHUNK_SIZE, '0'*64)
        self.assertEqual(context.exception.status, 422)
        self.assertEqual(len(self.svc.status(self.id)['received']), 0)

    def test_06_partial_upload_cannot_finalize(self):
        self.send_part(0)
        with self.assertRaises(UploadError) as context:
            self.svc.finalize(self.id)
        self.assertEqual(context.exception.status, 409)
        self.assertEqual(self.svc.status(self.id)['status'], 'UPLOADING')

    def test_07_wrong_part_length_rejected(self):
        with self.assertRaises(UploadError) as context:
            self.send_part(1, b'B'*5)
        self.assertEqual(context.exception.status, 422)

    def test_08_restart_preserves_chunks(self):
        self.send_part(0)
        new_service = Service(Path(self.temp.name))
        self.assertEqual(len(new_service.status(self.id)['received']), 1)
        new_service.put(self.id, 1, self.data[CHUNK_SIZE:2*CHUNK_SIZE], digest(self.data[CHUNK_SIZE:2*CHUNK_SIZE]))
        new_service.put(self.id, 2, self.data[2*CHUNK_SIZE:], digest(self.data[2*CHUNK_SIZE:]))
        with patch('backend.service.analyse_video', side_effect=dummy_video):
            self.assertEqual(new_service.finalize(self.id)['status'], 'COMPLETED')

    def test_09_corruption_on_disk_rejected_before_decode(self):
        for i in range(3): self.send_part(i)
        part = self.svc._part_path(self.id,1)
        part.write_bytes(b'Z'*CHUNK_SIZE)
        with patch('backend.service.analyse_video') as mocked:
            result = self.svc.finalize(self.id)
        self.assertEqual(result['status'], 'REJECTED')
        self.assertFalse(result['transfer_verified'])
        mocked.assert_not_called()

    def test_10_mismatched_file_digest_rejected(self):
        old = self.svc.start('different.mp4', len(self.data), 'f'*64)
        for i in range(old['chunk_count']):
            piece = self.data[i*CHUNK_SIZE:(i+1)*CHUNK_SIZE]
            self.svc.put(old['id'], i, piece, digest(piece))
        with patch('backend.service.analyse_video') as mocked:
            result = self.svc.finalize(old['id'])
        self.assertEqual(result['status'],'REJECTED')
        mocked.assert_not_called()

    def test_11_concurrent_duplicate_uploads(self):
        with ThreadPoolExecutor(max_workers=8) as exe:
            results=list(exe.map(lambda _:self.send_part(0),range(8)))
        self.assertEqual(sum(1 for r in results if not r['duplicate']),1)
        self.assertEqual(len(self.svc.status(self.id)['received']),1)

    def test_12_pause_resume(self):
        self.svc.pause(self.id)
        self.assertEqual(self.svc.status(self.id)['status'],'PAUSED')
        self.send_part(0)
        self.assertEqual(self.svc.status(self.id)['status'],'UPLOADING')

    def test_13_verify_state_crash_recovery(self):
        for i in range(3):self.send_part(i)
        with self.svc.db() as db:
            db.execute("UPDATE uploads SET status='VERIFYING' WHERE id=?",(self.id,))
        with patch('backend.service.analyse_video', side_effect=dummy_video):
            self.assertEqual(self.svc.finalize(self.id)['status'],'COMPLETED')
        with patch('backend.service.analyse_video') as mocked:
            self.assertEqual(self.svc.finalize(self.id)['status'],'COMPLETED')
        mocked.assert_not_called()

    def test_14_invalid_session_and_bounds(self):
        with self.assertRaises(UploadError):
            self.svc.status('../pwned')
        with self.assertRaises(UploadError):
            self.send_part(50)
        with self.assertRaises(UploadError):
            self.svc.start('bad.mp4',100*1024*1024+1,'f'*64)


@requires_video
class EndToEndVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GOOD.exists():
            from scripts.make_samples import generate
            generate(ROOT/'samples')

    def test_15_valid_30sec_source_and_target(self):
        result=analyse_video(GOOD)
        self.assertEqual(result['source']['source_frame_count'],900)
        self.assertEqual(result['target_30fps']['frame_count'],900)
        self.assertEqual(result['anomaly_count'],0)

    def test_16_synthetic_gap_detected_without_false_certainty(self):
        result=analyse_video(GAP)
        self.assertEqual(result['source']['source_frame_count'],870)
        self.assertEqual(result['target_30fps']['frame_count'],900)
        self.assertTrue(any(x['type']=='SOURCE_TIMESTAMP_GAP' for x in result['anomalies']))
        self.assertTrue(all(x['confidence']=='heuristic' for x in result['anomalies']))

    def test_17_invalid_container_rejected(self):
        with self.assertRaises(VideoValidationError):analyse_video(BAD)

    def test_18_real_upload_video_validation(self):
        with tempfile.TemporaryDirectory() as td:
            svc=Service(Path(td));body=GOOD.read_bytes()
            session=svc.start(GOOD.name,len(body),digest(body))
            for idx in range(session['chunk_count']):
                part=body[idx*CHUNK_SIZE:(idx+1)*CHUNK_SIZE]
                svc.put(session['id'],idx,part,digest(part))
            result=svc.finalize(session['id'])
            self.assertEqual(result['status'],'COMPLETED')
            self.assertTrue(result['transfer_verified'])
            self.assertTrue(result['video_validated'])
            self.assertTrue(svc.report(session['id'])['transfer']['sha256_match'])

    def test_19_invalid_video_with_valid_sha_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            svc=Service(Path(td));body=BAD.read_bytes();s=svc.start('bad.mp4',len(body),digest(body))
            svc.put(s['id'],0,body,digest(body))
            result=svc.finalize(s['id'])
            self.assertEqual(result['status'],'REJECTED')
            self.assertTrue(result['transfer_verified'])
            self.assertFalse(result['video_validated'])
            self.assertIn('parsing failed',result['error'].lower())


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.server=create_server('127.0.0.1',0,Path(self.temp.name))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        self.temp.cleanup()

    def req(self,method,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=20)
        conn.request(method,path,body=body,headers=headers or {})
        resp=conn.getresponse();result=json.loads(resp.read());status=resp.status;conn.close()
        return status,result

    @requires_video
    def test_20_http_protocol_and_chunks(self):
        status,info=self.req('GET','/api/health')
        self.assertEqual(status,200)
        payload=b'hello real HTTP'
        status,start=self.req('POST','/api/uploads',json.dumps(dict(filename='x.mp4',size=len(payload),sha256=digest(payload))),
                              {'Content-Type':'application/json'})
        self.assertEqual(status,201)
        id_=start['id']
        status,part=self.req('PUT',f'/api/uploads/{id_}/chunks/0',payload,{'X-Chunk-SHA256':digest(payload)})
        self.assertEqual(status,201)
        self.assertTrue(part['durably_stored'])
        status,part=self.req('PUT',f'/api/uploads/{id_}/chunks/0',payload,{'X-Chunk-SHA256':digest(payload)})
        self.assertEqual(status,200)
        self.assertTrue(part['duplicate'])
        status,info=self.req('GET',f'/api/uploads/{id_}')
        self.assertEqual(status,200)
        self.assertEqual(info['received_bytes'],len(payload))
        status,final=self.req('POST',f'/api/uploads/{id_}/finalize')
        self.assertEqual(status,422) # Correct bytes, but invalid video
        self.assertEqual(final['status'],'REJECTED')

    def test_21_deny_path_traversal_and_oversize(self):
        status,info=self.req('GET','/api/uploads/not-a-uuid')
        self.assertEqual(status,404)
        status,info=self.req('POST','/api/uploads',json.dumps(dict(filename='x.mp4',size=100*1024*1024+1,sha256='0'*64)),{'Content-Type':'application/json'})
        self.assertEqual(status,400)


if __name__=='__main__':unittest.main()

# Extra scenarios below are intentionally separate so all HTTP and source-video
# cases remain discoverable without supplementary pytest dependencies.
class MoreTests(unittest.TestCase):
    @requires_video
    def test_22_30_second_duration_enforced(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'short.mp4'
            r=subprocess.run(['ffmpeg','-nostdin','-y','-v','error','-f','lavfi','-i',
                              'testsrc2=size=160x90:rate=30:duration=2','-an','-c:v','mpeg4',str(p)],
                             capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            with self.assertRaisesRegex(VideoValidationError,'30-second'):
                analyse_video(p)

    @requires_video
    def test_23_invalid_video_report_separates_transfer_and_decode(self):
        with tempfile.TemporaryDirectory() as td:
            svc=Service(Path(td))
            payload=b'not-a-usable-video'
            s=svc.start('fake.mp4',len(payload),digest(payload))
            svc.put(s['id'],0,payload,digest(payload))
            svc.finalize(s['id'])
            report=svc.report(s['id'])
            self.assertTrue(report['transfer']['complete'])
            self.assertFalse(report['processing']['video_valid'])
            self.assertEqual(report['outcome'],'REJECTED')

    def test_24_simultaneous_finalize_no_false_success(self):
        with tempfile.TemporaryDirectory() as td:
            svc=Service(Path(td));data=b'x'* (CHUNK_SIZE+11)
            s=svc.start('vid.mp4',len(data),digest(data))
            for i in range(s['chunk_count']):
                p=data[i*CHUNK_SIZE:(i+1)*CHUNK_SIZE]
                svc.put(s['id'],i,p,digest(p))
            with patch('backend.service.analyse_video', side_effect=dummy_video):
                with ThreadPoolExecutor(max_workers=5) as exe:
                    results=list(exe.map(lambda _:svc.finalize(s['id']), range(5)))
            self.assertTrue(all(r['status']=='COMPLETED' for r in results))
            self.assertEqual(svc.report(s['id'])['outcome'],'COMPLETED')

    def test_25_cross_process_service_recovery_is_real_persistence(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            svc=Service(Path(td));content=b'cross-process recovery'
            s=svc.start('small.mp4',len(content),digest(content))
            svc.put(s['id'],0,content,digest(content))
            code=("from backend.service import Service; from pathlib import Path; import sys; "
                  "s=Service(Path(sys.argv[1])); x=s.status(sys.argv[2]); "
                  "print(x['received_bytes'],x['status'])")
            r=subprocess.run([sys.executable,'-c',code,td,s['id']],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertIn('22 UPLOADING',r.stdout)

class AdditionalVideoAndTransportTests(unittest.TestCase):
    @requires_video
    def test_26_legitimate_static_scene_is_not_called_confirmed_loss(self):
        p=ROOT/'samples'/'legitimate_static_scene.mp4'
        if not p.exists():
            from scripts.make_samples import generate
            generate(ROOT/'samples')
        result=analyse_video(p)
        repeated=[x for x in result['anomalies'] if x['type']=='VISUALLY_REPEATED_RUN']
        self.assertTrue(repeated)
        self.assertTrue(all(x['confidence']=='heuristic' for x in repeated))
        self.assertEqual(result['source']['source_frame_count'],900)

    @requires_video
    def test_27_truncated_container_is_not_accepted(self):
        with self.assertRaises(VideoValidationError):
            analyse_video(ROOT/'samples'/'truncated_invalid.mp4')

    def test_28_http_short_request_never_acknowledged(self):
        import socket, time
        with tempfile.TemporaryDirectory() as td:
            server=create_server('127.0.0.1',0,Path(td))
            worker=threading.Thread(target=server.serve_forever,daemon=True)
            worker.start()
            try:
                payload=b'X'* (CHUNK_SIZE-2)
                s=server.service.start('x.mp4',CHUNK_SIZE,digest(payload+b'YY'))
                raw=(f'PUT /api/uploads/{s["id"]}/chunks/0 HTTP/1.1\r\n'
                    f'Host: 127.0.0.1\r\nContent-Length: {CHUNK_SIZE}\r\n'
                    f'X-Chunk-SHA256: {digest(payload+b"YY")}\r\n\r\n').encode()
                sock=socket.create_connection(('127.0.0.1',server.server_port),timeout=5)
                sock.sendall(raw+payload[:8192]);sock.shutdown(socket.SHUT_WR)
                sock.close()
                time.sleep(.12)
                state=server.service.status(s['id'])
                self.assertEqual(state['received_bytes'],0)
                self.assertNotEqual(state['status'],'COMPLETED')
            finally:
                server.shutdown();server.server_close();worker.join(timeout=5)
