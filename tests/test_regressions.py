"""Current-release regressions. Mocked decoder tests are not video evidence."""
import hashlib
import http.client
import json
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import create_server
from backend.service import Service
from backend.video import VideoProcessingError, VideoValidationError, _run, analyse_video


class RecoveryTests(unittest.TestCase):
    def test_missing_decoder_retains_upload_for_retry(self):
        with tempfile.TemporaryDirectory() as td:
            svc = Service(Path(td)); data = b'preserve verified bytes'
            sha = hashlib.sha256(data).hexdigest()
            upload = svc.start('x.mp4', len(data), sha)
            svc.put(upload['id'], 0, data, sha)
            with patch('backend.service.analyse_video', side_effect=VideoProcessingError('Missing ffprobe')):
                with self.assertRaises(VideoProcessingError): svc.finalize(upload['id'])
            status = svc.status(upload['id'])
            self.assertEqual(status['status'], 'VERIFYING')
            self.assertTrue(status['transfer_verified'])
            self.assertFalse(status['video_validated'])
            with patch('backend.service.analyse_video', return_value={'video_valid': True}):
                self.assertEqual(svc.finalize(upload['id'])['status'], 'COMPLETED')
            self.assertIsNone(svc.status(upload['id'])['error'])

    def test_missing_executable_is_operational_failure(self):
        with patch('backend.video.subprocess.run', side_effect=FileNotFoundError):
            with self.assertRaises(VideoProcessingError): _run(['ffprobe'])

    def test_cleanup_preserves_lock_inode(self):
        with tempfile.TemporaryDirectory() as td:
            svc = Service(Path(td)); upload = svc.start('x', 1, '0'*64)
            with svc.lock(upload['id']): pass
            lock = Path(td)/'locks'/(upload['id']+'.lock'); inode = lock.stat().st_ino
            with svc.db() as db: db.execute('UPDATE uploads SET updated_at=0')
            self.assertEqual(svc.cleanup(), 1)
            self.assertEqual(lock.stat().st_ino, inode)


class VideoMetadataTests(unittest.TestCase):
    def test_invalid_metadata_is_explicit_validation_error(self):
        for duration in ['NaN','Infinity','unknown']:
            metadata = {'format': {'duration': '30'}, 'streams': [
                {'codec_type':'video', 'duration':duration, 'width':160, 'height':90}]}
            reply = subprocess.CompletedProcess([], 0, json.dumps(metadata), '')
            with self.subTest(duration=duration), patch('backend.video._run', return_value=reply):
                with self.assertRaises(VideoValidationError): analyse_video(Path('dummy'))

    def test_video_track_duration_overrides_audio_container_duration(self):
        reply = subprocess.CompletedProcess([], 0, json.dumps({'format':{'duration':'30'},
            'streams':[{'codec_type':'video','duration':'2','width':160,'height':90}]}), '')
        with patch('backend.video._run', return_value=reply):
            with self.assertRaisesRegex(VideoValidationError,'30-second'): analyse_video(Path('dummy'))


class HTTPFramingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server('127.0.0.1', 0, Path(self.temp.name))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True); self.worker.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join(5); self.temp.cleanup()

    def send(self, request):
        with socket.create_connection(('127.0.0.1',self.server.server_port), timeout=5) as sock:
            sock.sendall(request)
            result = b''
            while True:
                part = sock.recv(65536)
                if not part: break
                result += part
        return result

    def test_unread_body_does_not_become_second_request(self):
        response = self.send(b'POST /unknown HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n'
                             b'GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n')
        self.assertEqual(response.count(b'HTTP/1.1 '), 1)
        self.assertIn(b'404', response.split(b'\r\n')[0])
        self.assertIn(b'Connection: close',response)

    def test_ambiguous_lengths_rejected(self):
        response = self.send(b'POST /api/uploads HTTP/1.1\r\nHost: localhost\r\n'
                             b'Content-Length: 0\r\nContent-Length: 2\r\n\r\n{}')
        self.assertIn(b'411',response.split(b'\r\n')[0])

    def test_transfer_encoding_rejected(self):
        response = self.send(b'POST /api/uploads HTTP/1.1\r\nHost: localhost\r\n'
                             b'Transfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n')
        self.assertIn(b'400',response.split(b'\r\n')[0])
