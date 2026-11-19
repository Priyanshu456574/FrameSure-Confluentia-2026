"""Measure actual HTTP/TCP transfer, explicitly substituting video processing.

This is byte-transfer evidence only, never evidence of video correctness.
"""
import json
from pathlib import Path
from unittest.mock import patch
from scripts.benchmark import main

ROOT=Path(__file__).resolve().parent.parent

def no_video_evidence(path):
    return {'source': {'source_frame_count':None},
            'notice':'Decoder substituted for transport-only benchmark; video not validated'}

if __name__=='__main__':
    out=ROOT/'evidence'/'benchmark_results.json'
    with patch('backend.api.analyse_video',side_effect=no_video_evidence), \
         patch('backend.service.analyse_video',side_effect=no_video_evidence):
        main(out)
    data=json.loads(out.read_text())
    data['scope']='Transport-only: actual HTTP/TCP and SHA-256; video validator substituted, not executed'
    data['video_validation_executed']=False
    data['limitations'].append('success is byte-transfer success with a substituted decoder, not complete application validation')
    out.write_text(json.dumps(data,indent=2)+'\n')
