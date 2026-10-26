# Author and review an experiment

An experiment compares named configurations against the same frozen dataset. The baseline is the current behavior; candidates may change supported models or static instructions. A model's lower price alone does not establish acceptable quality.

Start with the editable `experiment.yaml` produced by [the sample](testing-the-sample.md). It contains `dataset_id` and `specification`. Compile it with `cutback compile-experiment SOURCE DESTINATION`, then use the existing `experiments submit`, `approve` and `run` commands against your authorized collector. Compilation parses bounded YAML without executing application code or making network requests.

The specification contains:

- `baseline` and `candidates`: unique keys, approved provider/model, static instruction and input/output bounds. Supported native SDK/model contracts remain in [providers](providers.md); experiment authoring does not widen that matrix.
- `metric`: primary deterministic measure, minimum, allowed regression and minimum independent sample/group counts.
- `criteria`: up to eight named deterministic criteria. Each has `metric`, `required` and a positive `weight`. An `exact_label` metric can select one top-level JSON `output_field`, such as `priority`. Required criteria must meet their own thresholds and allowed regression against baseline. A weighted summary cannot override their failure.
- `retain_case_outputs`: explicit opt-in to record generated outputs with inputs/labels in the encrypted payload store. Shared policy quotas, byte limits, revocation, expiry and project deletion still apply. Default is false. Payloads never enter the analytical result JSON.
- `budget`: modeled USD, request and wall-time ceilings. Judge calls and both orders count toward the same pre-call reservation budget. Unknown billing remains unknown; a lost billable call is not safe to repeat.
- `judge` and `judge_rubric`: optional provisional pairwise judging. `judge_swap_orders` evaluates both positions; `judge_require_evidence` rejects votes without an exact input quote of at least four characters. `judge_max_invalid_fraction` controls the reported health gate and prevents a recommendation passing when unhealthy. Quote membership proves citation location, not truth. Calibrate against independent human labels before relying on a judge.
- `staged`, `stage`, `sample_limit`, `split`, `predecessor_run_id`: optional smoke → pilot → full progression. Smoke/pilot require bounded validation cases; full requires all held-out test cases. Pilot/full bind the exact same dataset, variants, criteria, evaluator and price contract to a completed passing predecessor in the same project. Every stage needs a separate approval. The full-stage worst-case budget is still checked independently.

Reports include criterion scorecards, per-stratum metrics, and paired first-repeat case-score deltas with reproducible cluster resampling. The paired delta is descriptive for that dataset and does not replace the existing conservative population-quality gate. For non-additive metrics, the average per-case delta differs from the aggregate metric. Synthetic results and LLM preferences do not become verified customer claims.

Authorized administrators can request a bounded page of encrypted cases at `POST /v1/projects/{project_id}/experiments/{run_id}/cases/content` with a purpose reference. The dashboard exposes the same audited operation. Viewer credentials cannot read payloads. Failed/partial runs may contain only the records successfully retained before the stop; they are not automatically replayed.

Existing dev2 experiment approvals do not silently authorize the expanded dev3 schema. Outstanding old plans must be recreated and explicitly approved; historical metadata remains readable. Released migrations are unchanged.
