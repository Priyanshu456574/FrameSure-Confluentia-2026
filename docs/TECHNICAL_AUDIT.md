# Technical audit - version 2

## Baseline preservation and verification

Original reviewed commit 416cfbfc6430533cce724281c1ec233a2c519562 and its 67-file release manifest verified. New clone and release/v2.0.0 branch preserve the original release. Baseline 36 Python tests and four client checks passed before implementation. The later 44-test checkpoint was independently re-run; final suite has 46 passing cases.

| Severity | Finding | Change | Verification |
|---|---|---|---|
| High | Timeout was classified as invalid video and permanently rejected | VideoProcessingError for subprocess timeout, OS and signal failures | Simulated classification and actual FFmpeg deadline/retry |
| High | Repeated operational retries had no persisted terminal policy | SQLite counter, max three attempts, explicit FAILED with video_valid=null | Repeated timeouts, restart, crash-budget and idempotent terminal code |
| High | Earlier transfer flag could remain true if stored bytes corrupt after retry | Clear transfer_verified on byte rejection | Corruption-after-timeout regression |
| Medium | Client 503 rendered generic paused state instead of retry action | VERIFYING status, Retry processing button, accurate diagnostics | Client API-double 503/success checks |
| Medium | Stale report could remain available during a new processing attempt | Clear report/download and diagnostic fields at begin | Client regressions and code inspection |
| Medium | Completed-session restore left start control disabled after lookup | Restore controls after successful completed lookup | Code inspection; full browser recheck pending |
| Medium | Disconnect label and throughput wording could imply physical/wire measurement | Emulate 5s outage and ACK payload rate with scope note | Real updated dashboard observed; client emulator checks |
| Medium | Browser execution unavailable | Added reproducible five-scenario runner; recorded actual BLOCKED exit 2 | Genuine launch log, observed user completion screenshot/report |
| Medium | Network evidence previously limited to single socket cutoff | Added isolated latency/bandwidth/TCP/503 proxy profiles | Five passing real HTTP/video trials |

No architectural rewrite. Existing tests were preserved and extended, no assertions weakened. Prior evidence remains in the untouched previous release and Git parent; duplicate historical files are omitted from the new source snapshot. The official PS PDF, dependency folders and runtime databases are excluded.

Remaining: full browser outage/refresh/invalid/timeout matrix, physical packet loss/handoff, mobile/VFR fixtures, decoder isolation, authentication/TLS/global quotas, load testing, authenticated publication and late acceptance. Local elapsed times and a single fixture are not statistical performance evidence.
