# Browser evidence provenance

selection_verified.jpg: genuine observed browser state after the user selected/recovered an already completed valid sample. observed_completed_report.json: actual backend report for that observed session; its header/digest/frame count were compared with the user-downloaded report. These support observed completion and report availability, not automated execution of all scenarios.

file_selection_blocked.jpg: genuine earlier empty-file selection checkpoint. automated_results.json: five automated scenarios BLOCKED, exit 2. launch_error.log: actual Playwright failure with machine-specific paths redacted. There are no fabricated successful E2E screenshots or logs.

The user/browser session changed after the captured completed view. Later code changes were not fully browser-certified. Re-run tests/browser_e2e.py on an unrestricted machine to populate per-scenario screenshots/downloads/logs.
