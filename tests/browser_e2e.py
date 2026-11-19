"""Real Playwright scenarios. Exit 2 means launch/dependency BLOCKED, not passed.

Install requirements-browser.txt, then playwright install chromium. Optional
FRAMESURE_BROWSER_EXECUTABLE selects an already installed Chromium executable.
"""
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from backend.api import create_server
import backend.video as video
from scripts.benchmark import json_req

OUT=ROOT/'evidence/browser'
SCENARIOS=['A_normal_and_download','B_outage_recovery','C_refresh_reselection',
           'D_invalid_video','E_timeout_and_retry']
GOOD=ROOT/'samples/valid_30s_30fps.mp4'

class BrowserCases(unittest.TestCase):
    browser=None
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.server=create_server('127.0.0.1',0,Path(self.temp.name))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.context=self.browser.new_context(viewport={'width':1280,'height':900},accept_downloads=True)
        self.page=self.context.new_page();self.logs=[];self.responses=[];self.errors=[]
        self.page.on('console',lambda m:self.logs.append({'type':m.type,'text':m.text}))
        self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.page.on('response',lambda r:self.responses.append({'path':urlsplit(r.url).path,'status':r.status}))
        self.page.set_default_timeout(20000)
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}')

    def tearDown(self):
        name=self._testMethodName.removeprefix('test_')
        self.page.screenshot(path=str(OUT/(name+'.png')),full_page=True)
        (OUT/(name+'_console.json')).write_text(json.dumps({'console':self.logs,'pageerrors':self.errors,'responses':self.responses},indent=2)+'\n')
        self.context.close();self.server.shutdown();self.server.server_close();self.worker.join(5);self.temp.cleanup()

    def choose(self,path=GOOD,emulate=False):
        self.page.locator('#fileInput').set_input_files(str(path))
        self.page.wait_for_function("document.querySelector('#start').disabled === false")
        self.assertIn(path.name,self.page.locator('#fileInfo').inner_text())
        if emulate:self.page.locator('#strength').press('Home')
        else:self.page.locator('#chaos').uncheck()

    def wait_state(self,state):
        self.page.wait_for_function('(s)=>document.querySelector("#stateLabel").textContent===s',arg=state,timeout=60000)

    def session_id(self):
        return self.page.evaluate("JSON.parse(localStorage.getItem('framesure-active-v1')).id")

    def completed_report(self):
        self.wait_state('COMPLETED')
        self.assertEqual(self.page.locator('#progressText').inner_text(),'100.0%')
        self.assertIn('matching SHA-256',self.page.locator('#verified').inner_text())
        self.assertEqual(self.page.locator('#sourceFrames').inner_text(),'900')
        self.assertEqual(self.page.locator('#targetFrames').inner_text(),'900')
        self.assertFalse(self.errors,self.errors)
        return json_req(self.server.server_port,'GET',f'/api/uploads/{self.session_id()}/report')

    def test_A_normal_and_download(self):
        self.choose();self.page.locator('#start').click();report=self.completed_report()
        with self.page.expect_download() as info:self.page.locator('#download').click()
        path=OUT/'A_downloaded_report.json';info.value.save_as(str(path))
        self.assertEqual(json.loads(path.read_text()),report)
        self.assertEqual(report['transfer']['client_sha256'],hashlib.sha256(GOOD.read_bytes()).hexdigest())
        before=report['generated_at_unix'];id_=self.session_id()
        with ThreadPoolExecutor(max_workers=3) as exe:
            statuses=list(exe.map(lambda _:json_req(self.server.server_port,'POST',f'/api/uploads/{id_}/finalize'),range(3)))
        self.assertTrue(all(s['status']=='COMPLETED' for s in statuses))
        self.assertEqual(json_req(self.server.server_port,'GET',f'/api/uploads/{id_}/report')['generated_at_unix'],before)

    def test_B_outage_recovery(self):
        self.choose(emulate=True);self.page.locator('#start').click()
        self.page.wait_for_function('parseFloat(document.querySelector("#progressText").textContent)>0')
        before=[r['path'] for r in self.responses if '/chunks/' in r['path'] and r['status'] in (200,201)]
        self.page.locator('#disconnect').click()
        self.page.screenshot(path=str(OUT/'B_during_outage.png'),full_page=True)
        self.completed_report()
        all_acks=[r['path'] for r in self.responses if '/chunks/' in r['path'] and r['status'] in (200,201)]
        self.assertTrue(before)
        self.assertTrue(all(all_acks.count(path)==1 for path in before))
        self.assertIn('Emulated 5s outage',self.page.locator('#events').inner_text())

    def test_C_refresh_reselection(self):
        self.choose(emulate=True);self.page.locator('#start').click()
        self.page.wait_for_function('parseFloat(document.querySelector("#progressText").textContent)>0')
        self.page.locator('#pause').click();id_=self.session_id()
        before=json_req(self.server.server_port,'GET',f'/api/uploads/{id_}')['received_bytes']
        self.page.reload();self.choose(emulate=True)
        self.wait_state('RECOVERED');self.assertEqual(self.session_id(),id_)
        after=json_req(self.server.server_port,'GET',f'/api/uploads/{id_}')['received_bytes']
        self.assertGreaterEqual(after,before)
        self.page.locator('#start').click();self.completed_report()

    def test_D_invalid_video(self):
        self.choose(ROOT/'samples/not_a_video.mp4');self.page.locator('#start').click();self.wait_state('REJECTED')
        self.assertIn('REJECTED',self.page.locator('#videoValid').inner_text())
        report=json_req(self.server.server_port,'GET',f'/api/uploads/{self.session_id()}/report')
        self.assertTrue(report['transfer']['complete']);self.assertFalse(report['processing']['video_valid'])
        self.assertIn('parsing failed',report['error'].lower())

    def test_E_timeout_and_retry(self):
        original=video._run;fired=[]
        def deadline(args,timeout=video.FFMPEG_TIMEOUT):
            if Path(args[0]).name=='ffmpeg' and not fired:
                fired.append(True);return original(args,timeout=.000001)
            return original(args,timeout=timeout)
        self.choose()
        with patch('backend.video._run',side_effect=deadline):
            self.page.locator('#start').click();self.wait_state('VERIFYING')
            self.page.wait_for_function('document.querySelector("#start").textContent==="Retry processing"')
        self.assertEqual(self.server.service.status(self.session_id())['processing_attempts'],1)
        self.assertTrue(self.page.locator('#download').is_disabled())
        self.page.screenshot(path=str(OUT/'E_timeout_retryable.png'),full_page=True)
        self.page.locator('#start').click();self.completed_report()
        self.assertEqual(self.server.service.status(self.session_id())['processing_attempts'],2)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        reason='Playwright is not installed; install requirements-browser.txt and Chromium'
        (OUT/'automated_results.json').write_text(json.dumps({'status':'BLOCKED','reason':reason,'scenarios':{s:'BLOCKED' for s in SCENARIOS}},indent=2)+'\n')
        print(reason);return 2
    with sync_playwright() as playwright:
        try:
            options={'headless':True}
            if os.environ.get('FRAMESURE_BROWSER_EXECUTABLE'):options['executable_path']=os.environ['FRAMESURE_BROWSER_EXECUTABLE']
            browser=playwright.chromium.launch(**options)
        except Exception:
            # Keep raw diagnostics locally; redact account-specific directory names.
            reason=traceback.format_exc().replace(str(Path.home()),'<HOME>').replace(str(ROOT),'<PROJECT_ROOT>')
            (OUT/'launch_error.log').write_text(reason)
            (OUT/'automated_results.json').write_text(json.dumps({'status':'BLOCKED','exit_status':2,'reason':'Chromium launch failed; see launch_error.log',
                'scenarios':{s:'BLOCKED' for s in SCENARIOS}},indent=2)+'\n')
            print('BLOCKED: Chromium launch failed; no browser scenarios counted as passed.');return 2
        try:
            BrowserCases.browser=browser;stream=io.StringIO()
            result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BrowserCases))
            (OUT/'automated_run.log').write_text(stream.getvalue())
            summary={'status':'PASSED' if result.wasSuccessful() else 'FAILED','browser_version':browser.version,
                'executed':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'exit_status':0 if result.wasSuccessful() else 1}
            (OUT/'automated_results.json').write_text(json.dumps(summary,indent=2)+'\n');print(stream.getvalue())
            return summary['exit_status']
        finally:browser.close()

if __name__=='__main__':raise SystemExit(main())
