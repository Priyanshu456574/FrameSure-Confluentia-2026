# Network testing: three separate categories

## A. Application-layer emulation

The actual frontend functions are checked by tests/frontend_regressions.mjs: low-strength delay, request-failure branches, pause/outage gating and retry UI behavior use DOM/API doubles. These are client logic checks, not full browser workflows. The existing real dashboard exposes Emulate 5s outage; its remaining browser outage/reload demonstration is BLOCKED in this environment.

## B. Controlled impairment - executed

Command: `python3 -m scripts.network_test` with FFmpeg/FFprobe on PATH. It starts an isolated backend and a per-profile proxy, both on 127.0.0.1 with ephemeral ports and temporary storage. No host network setting changes.

Profiles: stable; 75 ms extra delay per PUT; 524,288 B/s upload body pacing; one TCP connection shutdown after 8,192 bytes; one injected HTTP 503. All five scenarios completed with matching SHA and real 900-frame decoding. Exact parameters/times/retries are in evidence/network/controlled_results.json and controlled_run.log. Throttled transfer took roughly 3.2 seconds in the recorded run, including analysis. Payload-submission counts are application counts, not packet capture.

These are userspace proxy faults. They do not simulate IP packet loss or physical radio percentages. Kernel tc/netem requires a Linux environment and elevated isolated network namespace; it was NOT run on this macOS host. Do not modify host routing, Wi-Fi or network shaping for this demonstration.

## C. Physical laptop/mobile procedure - NOT TESTED

Use a dedicated test laptop or phone, an isolated test backend reachable through an authenticated/TLS test setup, and a non-private 30-second sample. Keep one stable network available for recovery. Do not expose the raw unauthenticated prototype to the internet.

1. Record browser/device, network type, video SHA, time and session ID.
2. Start the upload and record acknowledged bytes/chunks.
3. Disconnect the test device's Wi-Fi briefly, then restore it. Wait for bounded retry/recovery.
4. In a separate authorized test, switch from Wi-Fi to a mobile hotspot and reselect the same file if a page reload is needed.
5. Compare final client/server SHA, byte count, session ID and decoded report.
6. Record actual screenshot/video and browser/server logs; redact private SSIDs/IPs before sharing.

| Scenario | Expected behavior | Actual observation | Evidence | Pass/fail criterion |
|---|---|---|---|---|
| Physical Wi-Fi interruption | Retain acknowledged pieces, recover safely | NOT TESTED | None | Final SHA matches and no false completion |
| Wi-Fi/mobile switching | Same session resumes or clear recoverable error | NOT TESTED | None | No silent partial acceptance; final report correct |
| Sustained degraded cellular | Bounded retry and explicit status | NOT TESTED | None | Success after recovery or honest recoverable/terminal status |

Do not fill Actual observation with expected results. Physical packet loss, handoff and real signal strength remain unverified until this procedure is executed on actual hardware.
