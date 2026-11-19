# Implementation decisions

The original modular architecture remains in place. Backend runtime dependencies are Python standard library plus FFmpeg/FFprobe executables. Frontend uses browser File, WebCrypto, Fetch and localStorage. See ARCHITECTURE.md for data flow and state design.

Version 2 adds a backward-compatible SQLite processing_attempts column. Per-session flock serializes all finalize attempts. Missing executables, OS execution errors, subprocess deadlines and negative signal exit codes map to VideoProcessingError; invalid container/decode outcomes map to VideoValidationError. A 503 leaves recoverable VERIFYING until the three-attempt limit. Terminal FAILED retains uploaded data and explicitly leaves video validity unknown. A completed report is reused idempotently.

Client wording distinguishes emulated application impairments from physical signal. ACK payload rate is an acknowledged-payload measurement rather than actual wire throughput. Report download resets on file change and at processing start. Restored completed sessions unlock controls after lookup. Existing visual design remains intact.

Known limitations: subprocess nonzero codec errors are treated as validation failure; unusual resource failures reported only as codec stderr may need richer classification. Internal exceptions remain recoverable until the same budget is exhausted. Processing is synchronous, the cap is per session rather than a global quota, and there is no decoder sandbox. Broad codec/VFR/resource-load experiments remain future work.
