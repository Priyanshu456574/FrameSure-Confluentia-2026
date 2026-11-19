# Reliability and security review (independent-minded checklist)

## Defenses present

- **False success**: finalization requires all index positions, exact expected chunk sizes, re-verification of stored chunk hashes, exact overall size and SHA-256; video decoder must pass.
- **Idempotent retries**: same index + content gets durable duplicate ACK; conflicting data returns 409. Cross-process POSIX file locks serialize operations.
- **Write crashes**: temp + `fsync` + `rename` + directory `fsync` before ACK, plus SQLite commit. A stale `.pending` assembly is reconstructible from original chunks.
- **Client crash or page refresh**: backend session persists and can be recovered after same-file hash check and user file re-selection.
- **Part reorder / out-of-order**: finalization orders `idx` (not arrival order).
- **Resources**: maximum one upload 100 MiB, chunk 256 KiB, 12 active sessions, ~30-second duration check, 4K dimensions, 30,000-frame report cap; execution limits on FFmpeg subprocesses.
- **Path traversal**: only server UUID/index identify storage. Client filename is display metadata. No dynamic HTML evaluation for file names/logs.
- **Banned states**: no PUT allowed when terminal or in `VERIFYING`.
- **Scientific claims**: `capture-time loss` is not asserted from source frame counts or SHA alone.

## Explicit limitations and recommended production remedies

| Residual issue | Risk | Production remediation |
|---|---|---|
| No authentication/authorization | Anyone who can reach API can view others' reports or consume space | Authenticated sessions, ownership checks, signed scoped tokens |
| Plain HTTP localhost only | Data unsafe if exposed over network | TLS and CSRF defenses behind reverse proxy |
| Limited global quota & unauthenticated users | Disk exhaustion/denial of service | Global quota, user quota, rate limiting, monitoring and automatic TTL sweep |
| FFmpeg runs on untrusted data | Potential decoder exploit/resource exhaustion | Dedicated low-privilege container/worker, seccomp, memory/CPU limits and up-to-date FFmpeg |
| Client hash is self-declared | Adversarial tampering could create a matching new digest | Trusted capture-side signed manifest and/or independent reference hash |
| Crash durability depends on POSIX filesystem | Network storage/Windows semantics may differ | Transactional object storage and lock coordinator |
| Synchronous video processing | HTTP finalize may time out on large/high-res content | Durable job queue and polling of analysis state |
| Full-file WebCrypto hash loads up to 100MiB in browser | Memory spike on low-memory device | Incremental SHA implementation or browser streaming via vetted crypto library |
| Browser backoff uses Math.random | Non-deterministic per-run UI experiment | Seeded test-run mode and logged random seed for browser benchmarks |
| Frame MD5 at 32×32 luma | False positives (static scenes) and misses (interpolated changes) | Report strong vs weak evidence, reference motion/CV manifest, ground-truth benchmark |
| ffprobe timestamps can be VFR or codec-dependent | Natural timing variation mistaken for missing frames | Per-container timing heuristics, PTS/DTS audit, VFR-aware thresholds |
| 1 source/tiny deterministic benchmark | Not enough for real-network reliability claims | 100+ randomized trials, multiple videos/phones and recorded tc/netem traces |

## Known incomplete tests

- Real physical packet loss, cellular handoff, Wi-Fi-to-cellular switch; these are not measured in the lab.
- 100MiB boundary performance and decoder resource-stress; size limit is enforced, not load-benchmarked.
- Browser Safari/WebKit cross-browser smoke, adverse mobile battery conditions, access controls and penetration tests.
- Fully labeled statistical source-frame anomaly precision/recall across codecs and VFR inputs.

**Release gating:** do not deploy outside loopback until the above security and scaling measures are implemented and tested.
