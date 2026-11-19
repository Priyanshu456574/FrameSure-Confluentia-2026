"""Create deterministic 30s 30fps examples (no external downloaded media)."""
import argparse
import subprocess
from pathlib import Path


def run(cmd):
    r=subprocess.run(cmd,capture_output=True,text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-1000:])


def generate(out:Path):
    out.mkdir(parents=True,exist_ok=True)
    good=out/'valid_30s_30fps.mp4'
    run(['ffmpeg','-nostdin','-hide_banner','-y','-loglevel','error','-f','lavfi',
         '-i','testsrc2=size=160x90:rate=30:duration=30','-an','-c:v','mpeg4',
         '-q:v','7',str(good)])
    # A synthetically introduced timestamp gap after filtering 30 frames.
    # It demonstrates a real metadata discontinuity, not proven camera loss.
    gap=out/'synthetic_timestamp_gap.mp4'
    run(['ffmpeg','-nostdin','-hide_banner','-y','-loglevel','error','-i',str(good),
         '-vf','select=not(between(n\\,300\\,329))', '-fps_mode','vfr',
         '-an','-c:v','mpeg4','-q:v','7',str(gap)])
    broken=out/'truncated_invalid.mp4'
    broken.write_bytes(good.read_bytes()[:256])
    static=out/'legitimate_static_scene.mp4'
    run(['ffmpeg','-nostdin','-hide_banner','-y','-loglevel','error','-f','lavfi',
         '-i','color=c=blue:s=160x90:r=30:d=30','-an','-c:v','mpeg4','-q:v','7',str(static)])
    nonsensical=out/'not_a_video.mp4' 
    nonsensical.write_bytes(b'this-is-not-a-video-container')
    return [good,gap,broken,static,nonsensical]


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=Path('samples'))
    args=p.parse_args()
    for f in generate(args.out):print(f,f.stat().st_size,'bytes')
