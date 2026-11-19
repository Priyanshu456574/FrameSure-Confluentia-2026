# Testing report - FrameSure 2.0.0

## PASSED: complete Python suite

Command: `python3 -m scripts.validate` with Python 3.11.9 and FFmpeg/FFprobe 7.0 on macOS. **46 passed, 0 skipped, 0 failures, 0 errors**, 6.256 seconds. Exact output: evidence/test_results.log; structured summary: evidence/validation_summary.json.

The preserved 36-test reviewed baseline was re-run first. The intermediate timeout checkpoint passed 44 tests. The final suite adds ten tests over baseline: subprocess timeout/OS/signal classification; verified-data preservation and real report retry; repeated timeout exhaustion without a corruption claim; persisted retry counter; corruption after timeout clears old verification; 503 then retry; an actual FFmpeg deadline/retry; and concurrent duplicate HTTP finalize reusing a real report. Existing valid/corrupt/truncated/static/gap, hash, chunk, short-body, replay, concurrency and restart assertions remain intact.

A real FFmpeg process is given a 1 microsecond deadline in a test-only wrapper. It times out, preserves a VERIFYING session and verified data, then succeeds under the normal deadline. This is actual execution, not an invented timeout success. Other unit cases deliberately simulate errors; they are identified in their source. Fresh timeout evidence is in evidence/timeout_recovery.json.

## PASSED: frontend checks

`node tests/frontend_regressions.mjs`: seven behavior checks and JavaScript parsing passed. Checks use explicit DOM/API doubles: selection locking, ignored selection during upload, stale report reset, stale hash suppression, 503 retry UI, completed progress/frame rendering and emulator delay/failure/outage gates. These are not full browser compatibility evidence.

## PARTIALLY VERIFIED: actual browser use

The user completed a valid upload/download. The observed dashboard recovered session a87f4f10-0556-46f3-8a2e-f0b915160185, showed 100% durable data, matching client/server SHA, decoder success, 900 source/target frames and no anomalies. The user-provided downloaded JSON header was checked against the actual server report and valid sample digest. A screenshot is in evidence/browser/selection_verified.jpg; server report is observed_completed_report.json. No console errors were observed in that view.

This records observed user execution and reselected completed-session recovery. It is not proof of a fresh automated upload or interrupted mid-upload refresh. Later source changes (report reset and controls) were regression-tested with API doubles, not re-certified in a real browser.

## BLOCKED: remaining automated browser scenarios

Command: `python3 tests/browser_e2e.py`, with Playwright 1.65.2 and installed Chromium executable selected by FRAMESURE_BROWSER_EXECUTABLE. Actual exit status **2**, Chromium launch failed (SIGABRT/permission restrictions). Automated scenario results are BLOCKED, never passed. Raw diagnostics with machine path redactions are included in evidence/browser/launch_error.log. Browser session controls also exposed different/empty tabs after the user completed the upload, while extension file-URL restrictions and stopped native-picker control prevented reliable reselection. No security setting was changed.

The portable runner implements A normal/download/duplicate finalize, B outage recovery, C refresh/reselection, D invalid file, E real timeout/retry. These five browser scenarios still need an unrestricted runner or manual execution. Hosted CI includes the job but has not executed here.

## PASSED: controlled proxy impairment

Command: `python3 -m scripts.network_test`. A dedicated localhost proxy and backend use ephemeral ports and temporary data, with no host-network changes. All five profiles passed with real decoding and matching SHA:

| Profile | Parameters | Retries | Seconds |
|---|---|---:|---:|
| stable | {} | 0 | 0.323 |
| latency | {"latency_seconds": 0.075} | 0 | 0.765 |
| throttled | {"bytes_per_second": 524288} | 0 | 3.217 |
| TCP interruption | {"disconnect_once": true} | 1 | 0.225 |
| transient request failure | {"http503_once": true} | 1 | 0.234 |

These are application/proxy scheduling faults, not IP packet loss. Physical Wi-Fi/mobile switching and actual radio degradation are NOT TESTED. Kernel tc/netem was not run on the macOS host. See NETWORK_TESTING_GUIDE.md.

## PASSED: benchmark reproduction

Command: `python3 -m scripts.benchmark --out evidence/full_video_benchmark.json`. Four actual HTTP/TCP trials, same video and real validation for both strategies:

| Scenario | Strategy | Payload bytes | Retries | Seconds |
|---|---|---:|---:|---:|
| stable | single-request | 1386829 | 0 | 0.289 |
| stable | resumable-chunks | 1386829 | 0 | 0.245 |
| injected_tcp_disconnect_at_~50pct | single-request | 2091341 | 1 | 0.238 |
| injected_tcp_disconnect_at_~50pct | resumable-chunks | 1567053 | 1 | 0.248 |

Stable payload is 1,386,829 bytes. In the single interrupted case, resumable transfer submits 1,567,053 bytes versus 2,091,341 baseline bytes, a 524,288-byte difference. This one deterministic loopback test does not establish statistical reliability or universal speed. Peak memory was not measured.

## Other quality checks and pending work

Python compileall and git diff whitespace checks pass. Source credential-pattern/path scans and clean archive hash verification are part of release validation. The complete suite is run again from a clean extraction before delivery; outer RELEASE_VALIDATION.json records that run and manifest check. The previous ZIP/PDF and Git parent are preserved. Public GitHub publication and hosted CI are pending; no email sent and no late acceptance assumed. No dependency CVE scan, decoder sandbox, multiprocess load campaign or broad mobile/VFR evaluation is claimed.
