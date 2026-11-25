# B05 clean-commit engineering evidence

The verification uses code 509e8007218c306d0e0639d96317537676570a0b (clean before/after).
All data, users, reviewer fixtures and models are explicitly synthetic. No external model/source
requests or new charges occurred. `verification.json` model_calls=0 means real external calls,
not local FunctionModel invocations. Recovery receipts tagged `offline=false` and deepseek-flash
exercise the paid-accounting branch with a local FunctionModel; their amounts are constructed
ledger fixtures, not actual provider bills. The explicit worker network-denial is in
`tests/fixtures/jobs_worker.py`. Seven crash/recover/read triples use 21 distinct subprocesses.

Raw synthetic events, process logs and receipt/checkpoint summaries are copied without edits.
Business/DBOS SQLite files and private bootstrap paths are not published. Tests assert counts,
return codes (-9 for SIGKILL, 0 for restart/read), exact business snapshots and hashes against
those private local test databases. Per-case source manifests and output digests are retained.

The six full-suite command logs and driver are included; the driver binds SHA and timestamps.
The 43-file B05-01 backend-selection evidence index remains separate and unchanged. Its official
source snapshot preserves original whitespace; no source-code whitespace error was accepted.

This evidence proves offline behavior only, not true human approval, model quality, real IdP,
production deployment or historical six-notice migration. These remain separately blocked.
