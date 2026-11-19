"""Run discoverable tests, retain exact stdout and machine-readable outcomes."""
import datetime
import io
import json
import platform
import shutil
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def main():
    started=time.perf_counter(); stream=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    log=stream.getvalue(); (ROOT/'evidence'/'test_results.log').write_text(log)
    data={'date_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'python':platform.python_version(),'platform':platform.platform(),
          'tests_discovered':result.testsRun, 'passed':result.testsRun-len(result.skipped)-len(result.failures)-len(result.errors),
          'skipped':len(result.skipped),'failures':len(result.failures),'errors':len(result.errors),
          'elapsed_seconds':round(time.perf_counter()-started,3),
          'ffmpeg_available':bool(shutil.which('ffmpeg')),'ffprobe_available':bool(shutil.which('ffprobe')),
          'skip_reasons':[{'test':str(test),'reason':reason} for test,reason in result.skipped]}
    (ROOT/'evidence'/'validation_summary.json').write_text(json.dumps(data,indent=2)+'\n')
    print(log); print(json.dumps(data,indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)

if __name__=='__main__': main()
