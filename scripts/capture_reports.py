"""Reproduce sample reports and an actual FFmpeg timeout/retry evidence record."""
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from backend.service import Service
from backend.video import VideoProcessingError
import backend.video as video

ROOT=Path(__file__).resolve().parent.parent

def upload(service,name):
    data=(ROOT/'samples'/name).read_bytes()
    session=service.start(name,len(data),hashlib.sha256(data).hexdigest())
    for i in range(session['chunk_count']):
        part=data[i*session['chunk_size']:(i+1)*session['chunk_size']]
        service.put(session['id'],i,part,hashlib.sha256(part).hexdigest())
    return session['id']

def main():
    with tempfile.TemporaryDirectory() as td:
        service=Service(Path(td))
        for sample,out in [('valid_30s_30fps.mp4','valid'),('synthetic_timestamp_gap.mp4','gap'),('not_a_video.mp4','rejected')]:
            id_=upload(service,sample);service.finalize(id_)
            (ROOT/f'evidence/{out}_integrity_report.json').write_text(json.dumps(service.report(id_),indent=2)+'\n')
        id_=upload(service,'valid_30s_30fps.mp4');original=video._run;fired=[]
        def deadline(args,timeout=video.FFMPEG_TIMEOUT):
            if Path(args[0]).name=='ffmpeg' and not fired:
                fired.append(True);return original(args,timeout=.000001)
            return original(args,timeout=timeout)
        with patch('backend.video._run',side_effect=deadline):
            try:service.finalize(id_)
            except VideoProcessingError:pass
        before=service.status(id_)
        assert before['status']=='VERIFYING' and before['transfer_verified']
        after=service.finalize(id_);assert after['status']=='COMPLETED'
        evidence={'mechanism':'First real FFmpeg call uses a 1 microsecond subprocess deadline; next attempt uses normal deadlines',
                  'before_retry':before,'after_retry':after,'report':service.report(id_)}
        (ROOT/'evidence/timeout_recovery.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print('Generated valid, gap, rejected and actual timeout/retry evidence')

if __name__=='__main__':main()
