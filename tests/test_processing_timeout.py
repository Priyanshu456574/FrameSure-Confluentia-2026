"""Operational failures must not become claims of damaged source video."""
import hashlib
import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import create_server
from backend.service import Service, UploadError, MAX_PROCESSING_ATTEMPTS
from backend.video import VideoProcessingError, _run, analyse_video

ROOT=Path(__file__).resolve().parent.parent
HAS_VIDEO=bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))

class ErrorClassificationTests(unittest.TestCase):
    def test_subprocess_timeout_is_retryable(self):
        with patch('backend.video.subprocess.run',side_effect=subprocess.TimeoutExpired(['ffmpeg'],.01)):
            with self.assertRaisesRegex(VideoProcessingError,'timed out'): _run(['ffmpeg'],timeout=.01)

    def test_temporary_os_failure_is_retryable(self):
        with patch('backend.video.subprocess.run',side_effect=OSError(12,'Cannot allocate memory')):
            with self.assertRaises(VideoProcessingError): _run(['ffmpeg'])

    def test_killed_process_is_retryable(self):
        with patch('backend.video.subprocess.run',return_value=subprocess.CompletedProcess(['ffmpeg'],-9,'','')):
            with self.assertRaisesRegex(VideoProcessingError,'signal 9'): _run(['ffmpeg'])

@unittest.skipUnless(HAS_VIDEO,'FFmpeg/FFprobe required for lifecycle evidence')
class TimeoutLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.svc=Service(Path(self.temp.name))
        self.data=(ROOT/'samples/valid_30s_30fps.mp4').read_bytes()
        s=self.svc.start('valid.mp4',len(self.data),hashlib.sha256(self.data).hexdigest());self.id=s['id']
        for i in range(s['chunk_count']):
            p=self.data[i*s['chunk_size']:(i+1)*s['chunk_size']]
            self.svc.put(self.id,i,p,hashlib.sha256(p).hexdigest())

    def tearDown(self): self.temp.cleanup()

    def expire(self):
        with patch('backend.video.subprocess.run',side_effect=subprocess.TimeoutExpired('ffmpeg',.01)):
            with self.assertRaises(VideoProcessingError):self.svc.finalize(self.id)

    def test_timeout_retains_data_and_retry_generates_one_real_report(self):
        self.expire();s=self.svc.status(self.id)
        self.assertEqual(s['status'],'VERIFYING');self.assertTrue(s['transfer_verified'])
        self.assertFalse(s['video_validated']);self.assertEqual(s['processing_attempts'],1)
        self.assertEqual((self.svc.root/'final'/f'{self.id}.video').read_bytes(),self.data)
        self.assertEqual(s['received_bytes'],len(self.data))
        with self.assertRaises(UploadError):self.svc.report(self.id)
        self.assertEqual(self.svc.finalize(self.id)['status'],'COMPLETED')
        report=self.svc.report(self.id)
        self.assertEqual(report['processing']['source']['source_frame_count'],900)
        self.assertEqual(report['processing']['target_30fps']['frame_count'],900)
        with patch('backend.service.analyse_video') as decoder:
            self.assertEqual(self.svc.finalize(self.id)['status'],'COMPLETED')
            decoder.assert_not_called()
        self.assertEqual(self.svc.report(self.id),report)

    def test_repeated_timeouts_eventually_fail_without_corruption_claim(self):
        for _ in range(MAX_PROCESSING_ATTEMPTS-1):self.expire()
        with patch('backend.service.analyse_video',side_effect=VideoProcessingError('timeout')):
            status=self.svc.finalize(self.id)
        self.assertEqual(status['status'],'FAILED');self.assertTrue(status['transfer_verified'])
        self.assertEqual(status['processing_attempts'],MAX_PROCESSING_ATTEMPTS)
        report=self.svc.report(self.id)
        self.assertIsNone(report['processing']['video_valid'])
        self.assertEqual(report['processing']['error_class'],'operational_failure')
        with patch('backend.service.analyse_video') as decoder:
            self.assertEqual(self.svc.finalize(self.id)['status'],'FAILED');decoder.assert_not_called()
        self.assertEqual(self.svc.report(self.id),report)
        self.assertEqual((self.svc.root/'final'/f'{self.id}.video').read_bytes(),self.data)

    def test_attempt_count_survives_restart(self):
        self.expire();self.svc=Service(Path(self.temp.name))
        self.assertEqual(self.svc.status(self.id)['processing_attempts'],1)
        self.assertEqual(self.svc.finalize(self.id)['processing_attempts'],2)

    def test_actual_ffmpeg_deadline_then_real_retry(self):
        import backend.video as video
        original=video._run; deadlines=[]
        def tiny_deadline(args,timeout=video.FFMPEG_TIMEOUT):
            if Path(args[0]).name=='ffmpeg' and not deadlines:
                deadlines.append(True)
                return original(args,timeout=.000001)
            return original(args,timeout=timeout)
        with patch('backend.video._run',side_effect=tiny_deadline):
            with self.assertRaisesRegex(VideoProcessingError,'timed out'):
                self.svc.finalize(self.id)
        self.assertEqual(self.svc.status(self.id)['status'],'VERIFYING')
        self.assertEqual(self.svc.finalize(self.id)['status'],'COMPLETED')
        self.assertEqual(self.svc.report(self.id)['processing']['source']['source_frame_count'],900)

    def test_corruption_after_timeout_clears_old_verification_flag(self):
        self.expire();self.svc._part_path(self.id,0).write_bytes(b'broken')
        with patch('backend.service.analyse_video') as decoder:
            status=self.svc.finalize(self.id);decoder.assert_not_called()
        self.assertEqual(status['status'],'REJECTED');self.assertFalse(status['transfer_verified'])

    def test_http_503_then_successful_retry(self):
        server=create_server('127.0.0.1',0,self.svc.root)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def request():
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            c.request('POST',f'/api/uploads/{self.id}/finalize');r=c.getresponse();body=json.loads(r.read());code=r.status;c.close();return code,body
        try:
            with patch('backend.service.analyse_video',side_effect=VideoProcessingError('controlled timeout')):
                self.assertEqual(request()[0],503)
            self.assertEqual(server.service.status(self.id)['status'],'VERIFYING')
            code,body=request();self.assertEqual(code,200);self.assertEqual(body['status'],'COMPLETED')
        finally:server.shutdown();server.server_close();worker.join(5)

    def test_duplicate_http_finalize_reuses_real_video_report(self):
        from concurrent.futures import ThreadPoolExecutor
        from scripts.benchmark import json_req
        self.svc.finalize(self.id)
        original=self.svc.report(self.id)
        server=create_server('127.0.0.1',0,self.svc.root)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            with patch('backend.service.analyse_video') as decoder, ThreadPoolExecutor(max_workers=3) as pool:
                results=list(pool.map(lambda _:json_req(server.server_port,'POST',f'/api/uploads/{self.id}/finalize'),range(3)))
                self.assertTrue(all(s['status']=='COMPLETED' for s in results));decoder.assert_not_called()
            self.assertEqual(server.service.report(self.id),original)
            self.assertEqual(server.service.status(self.id)['processing_attempts'],1)
        finally:server.shutdown();server.server_close();worker.join(5)
