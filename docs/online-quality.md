# Authorized online content quality

Source-checkout implementation; the published `0.1.0.dev5` wheel does not acquire new features from a GitHub merge. Acceptance and publication state belong in [progress](progress.md).

Online quality extends the [metadata monitoring](online-monitoring.md) plane. Capture remains off by default. The worker needs explicitly enabled encrypted storage, `CUTBACK_ALLOW_PAYLOADS=true`, `CUTBACK_ONLINE_EXECUTION=true` and `CUTBACK_QUALITY_EXECUTION=true`. Every rule additionally requires an active scoped capture policy, an approving administrator who still has access, explicit content-use approval, a bounded lifetime, sample and evaluation caps. Model judging also needs a pinned prompt, authorized vendor, native qualified model/surface, price-catalog hash, total request cap and total dollar cap. Saving a rule does not activate a worker or supply credentials.

## Three distinct evaluations

`nonempty_output` checks only whether the authorized captured output contains non-whitespace text. `output_schema` checks the captured string against a pinned root prompt's JSON schema. They make no inference requests and are labeled `cutback_technical_contract`.

`model_judge` assesses captured input/output through the existing native executor and canonical pre-call budget ledger. It is labeled `cutback_model_judge`; it is neither an independent reference label nor a security guarantee. The prompt graph must declare exactly two string variables, `input` and `output`, and this exact root schema:

```json
{"type":"object","properties":{"outcome":{"type":"string","enum":["passed","failed","unknown"]},"evidence_quote":{"type":"string"}},"required":["outcome","evidence_quote"],"additionalProperties":false}
```

The prompt is an immutable encrypted registry version, including pinned component dependencies. A decided judge result must include an exact substring from the captured input or output. A hallucinated quote is an evaluator error, not an application failure. A matching quote alone does not validate the judge's reasoning. Treat captured text as untrusted data when writing the rubric. Provider refusals, malformed responses, incomplete outputs, schema-work limits and billing uncertainty do not become application failures.

Create rules with `POST /v1/projects/{project_id}/quality-rules`; inspect and pause/resume through the corresponding list, evaluations and switch endpoints. Revisions use compare-and-swap; changes require a new immutable rule version. Managed score-monitor rules cannot be separately switched or superseded through the metadata API. Alerts target the returned `online_rule_id` and use the same existing closed-window/incident machinery.

## Costs, crashes and authority

The queue, backfill, stable sampling, per-project locks and retention limits are shared with the canonical monitoring plane. Ingestion records a bounded outbox entry; it never runs a judge. Each native request reserves total requests and worst-case dollars durably before SDK execution. Concurrent workers cannot spend the last budget slot twice. Configuration is prepared before reservation. Requests have no native retry; a crashed charged lease becomes unknown with uncertain billing and is never automatically replayed. An admission deadline prevents stale queued work from starting paid calls. Known spend and reserved upper cost remain separate; unknown usage stays unknown. Judge costs are shown in the quality ledger; they are not fabricated as complete application cost per successful task.

The native executors use their existing worker-only `CUTBACK_EXPERIMENT_OPENAI_KEY` / `CUTBACK_EXPERIMENT_ANTHROPIC_KEY` configuration. This does not enable experiment execution. The supported pinned SDK/model/surface limits remain those in [providers](providers.md). Optional local inference uses the existing separately configured local endpoint and explicit upper-cost contract. HTTP is never configured from captured content.

Current content rights, creator role, graph dependencies, vendor scope, source identity and protected confirmation groups are checked before reading and again before a call. Rights are checked again before accepting evidence or retaining a response. Revocation during execution preserves any known charge and discards newly unauthorized evidence/output. Missing captured input cannot be invented: normalized inputs join the persistent dataset-family registry, and active protected confirmation groups are excluded before judging. Online use records validation exposure; those examples cannot later be casually recycled as fresh independent confirmation.

## Retention and observed shifts

Raw judge output is discarded by default. `retain_judge_output=true` reserves a slot in the same capture quota before execution and stores the response encrypted under that policy's lifetime. An administrator can request it with an explicit investigation purpose; every read rechecks current policy and is audited. Metadata export contains no raw text or storage-object references. Project deletion waits for live quality leases, deletes encrypted objects and removes quality records. Raw receipt retention preserves cumulative rule budget totals; it does not recreate deleted source records or outcome distributions.

The comparison endpoint accepts two ordered, nonoverlapping windows spanning at most 30 days, for one exact rule version. It reports retained selected counts, distinct outcome/error/missing/unknown states, known/unknown cost receipts, decided failure fractions, and optional category distribution change. Supported categories are validated release/service identifiers and **opt-in coarse captured-input size buckets**. Missing categories remain null; more than 100 categories disables category detail instead of showing a partial distribution as complete. No semantic drift model, statistical significance, causation, full-population coverage, or quantiles are inferred. Changes to a rubric require a new version; its scores are not silently pooled with the old rule.

Schema evaluation is bounded by bytes, depth, nodes, array/object sizes, supported schema keywords and validation work. Arbitrary references, regex-based execution and custom evaluator code are unsupported. These limits and the independent sample are exercised against actual disposable PostgreSQL, synthetic native transports and the authenticated React browser. They do not qualify real customer quality or authorize production traffic, provider spend or automatic rollback.

## Operator settings

The default Compose stack remains metadata-only; an owner must configure shared encrypted storage and worker-only credentials in a private override before opting into quality execution. Helm adds `execution.quality=false`. Enabling it requires payloads, the online worker and an enabled worker deployment; provider keys named in `execution.providers` are mounted only on the worker when quality or experiments are enabled. Keep egress restricted to the explicitly approved provider. Migration `0012` adds the quality ledger and score-origin identity. Downgrade refuses while quality rules or internal-origin scores exist, so rollback cannot silently discard a billable ledger or collapse internal and external evidence. Export and explicitly delete affected project data before a downgrade.
