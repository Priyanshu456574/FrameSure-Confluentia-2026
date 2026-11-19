"""Check required runtime tools before a full video validation run."""
import shutil
import subprocess
import sys

def main():
    print('Python:',sys.version.split()[0])
    missing=[]
    for tool in ('ffmpeg','ffprobe'):
        if not shutil.which(tool):
            print(tool+': MISSING'); missing.append(tool)
        else:
            result=subprocess.run([tool,'-version'],capture_output=True,text=True,check=True)
            print(result.stdout.splitlines()[0])
    if missing:
        print('Install FFmpeg (includes FFprobe), then retry; no complete video validation can be claimed.')
        return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
