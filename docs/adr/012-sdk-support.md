# ADR-012: Compatibility and duplicate/retry semantics

Accepted with a release blocker. OpenAI SDK 2.29.0 is pinned and tested with synthetic HTTP for sync/async Responses and Chat Completions, streams, missing usage and provider errors. Bootstrap is singleton per process/config; explicit close restores hooks. Existing externally wrapped methods are rejected. Native SDK decorators are allowed by implementation path. No global OTel provider is replaced.

The resource hook currently emits one SDK **operation**, not one HTTP retry attempt. Internal retry-level billing/attribution, framework wrappers and all helper streams remain unsupported. The nullable retry number must not be filled with a fictitious zero. Complete per-attempt telemetry requires lower-layer hooks correlated to resource operations with response-usage privacy tests. M2 remains incomplete until this and approved live acceptance are demonstrated.
