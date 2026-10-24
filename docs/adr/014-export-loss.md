# ADR-014: Bounded best-effort export

Accepted initial delivery policy. Queue 512/batch 50, daemon network thread, 300ms default HTTP phase timeout, body/batch caps, zero retries and explicit loss counters. This is a bounded retry policy (zero) that favors inference isolation. `flush` waits only to a supplied deadline; in-flight export may finish later. No disk spool or exactly-once delivery claim. Collector deduplicates repeated IDs. Later retry/backoff or local OTel agent support must preserve memory/time bounds and expose losses, and must be benchmarked in serverless runtimes.
