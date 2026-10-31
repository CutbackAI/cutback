# ADR 027: bind learned routing to a qualified native application

Status: source implementation under qualification; migrations 0001–0027 unchanged.

Training learns a policy, not an application change. Deterministic v16 adds an
optional exact source binding to the v15 training/fit contract. Existing v15
serialization omits this field, preserving released approval hashes.

Qualification parses the complete bounded producer module and accepts its fixed
AST. It never executes application code. The producer and sibling `routing.json`
must be tracked in one clean Git snapshot. The initial policy contains one native
baseline. Instruction, provider, request frame and input domain stay fixed;
alternative model/output bounds retain the qualified catalog and cannot increase
output capacity. The runtime preserves the native response, exception and owner's
SDK retry setting. It adds no retry, failover or streaming conversion.

The collector cannot inspect an application's current disk. It authenticates
training receipts, current content permission, the frozen source contract,
validation and one fresh test confirmation. A purpose-scoped administrator may
read a proposal only when recorded native costs are complete, the selected policy
is cheaper on that sample and all required quality gates pass. Instructions stay
encrypted; retained recommendation comparisons use the same redacted projection.

The application agent rechecks both current clean trees, the actual checked
consumer, exact configuration bytes and rollback. It refreshes collector authority
before a source review. A local JSON file or local check receipt cannot independently
attest collector permission or customer quality. An optional local branch is
created without overwriting a different ref; nothing deploys or pushes the app.

The supported consumer is sync, non-streaming, plain-text OpenAI Responses with
the pinned SDK versions. Arbitrary wrappers, async/streams, custom gateways,
frameworks and broader routers remain separate qualifications. Hosting, fitting,
amortized setup and full-task costs remain unknown. Synthetic demonstrations never
become customer claims.
