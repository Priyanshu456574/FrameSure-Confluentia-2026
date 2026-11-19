# Browser demonstration and remaining tests

The user selected/uploaded the valid sample and downloaded a real report. The observed dashboard recovered a COMPLETED session (a87f4f10-0556-46f3-8a2e-f0b915160185), 100% acknowledged, matching SHA, 900 source/target frames and no heuristic anomalies. The screenshot and report are genuine. This is observed user execution, not a newly agent-initiated automated upload.

Remaining full browser cases are BLOCKED: the native picker session was stopped, extension file upload requires file-URL permission, standalone Playwright Chrome aborts under execution restrictions, and the later browser connection exposes a different empty tab. No security permissions were changed or successful browser scenarios fabricated. See evidence/browser/automated_results.json and launch_error.log. A direct browser file-input metadata read was also unavailable in the restricted DOM view; filename/hash verification relied on visible dashboard text.

## Execute the portable suite on an unrestricted local machine

Install requirements-browser.txt, run `python3 -m playwright install chromium`, then `python3 tests/browser_e2e.py`. The runner creates isolated server sessions and uses the hidden input's supported set_input_files method. It captures genuine screenshots, console/page errors, request statuses and downloaded JSON only after real execution. Exit 2 is BLOCKED; exit 1 is FAILED; exit 0 is PASSED. Hosted CI contains this job but has not run.

## Four-minute live demo

| Time | Action and explanation |
|---|---|
| 0:00-0:45 | Fresh browser context; select valid_30s_30fps.mp4. Explain client SHA, durable 256 KiB pieces and server byte gate. |
| 0:45-1:30 | Upload under application emulator, click Emulate 5s outage. Show acknowledged progress survives; in-flight pieces may finish. |
| 1:30-2:05 | Refresh mid-upload, reselect identical file, show same session and recovery. |
| 2:05-2:40 | Show completed matching SHA, actual decoded frames and JSON download. Distinguish source from 30 FPS resampling. |
| 2:40-3:10 | Invalid file: REJECTED. It may be byte-perfect while not a valid video. |
| 3:10-3:40 | Run controlled deadline fixture from browser_e2e.py. Show retryable VERIFYING then success; three persistent failures become FAILED with unknown validity. |
| 3:40-4:00 | Present current evidence counts and proxy/benchmark scope; disclose pending physical network and hosted CI. |

Do not claim these remaining live steps passed merely because this script exists. Do not add production fault-injection endpoints; the timeout fixture is confined to the test process.
