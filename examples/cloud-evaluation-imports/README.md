# Import an existing cloud text-evaluation export

This source feature passed its local component gates and is absent from PyPI dev5.
Public publication and exact-head hosted acceptance remain separate; see the
[qualification receipt](../../docs/evidence/cloud-text-import-acceptance.json).
Import
existing files without a cloud login or evaluation request. Preview is offline;
upload and content reads use the existing explicit encrypted import/review flow.

## Try the independent files

From the Cutback source environment:

```sh
cutback datasets preview-import tests/fixtures/imports/azure-ai-evaluation-1.18.10-results.json --profile azure-ai-evaluation-1.18.10-results.v1
cutback datasets preview-import tests/fixtures/imports/vertexai-1.153.1-missing.jsonl --profile vertexai-1.153.1-metrics-jsonl.v1
cutback datasets preview-import tests/fixtures/imports/bedrock-automated-model.jsonl --profile bedrock-automated-model-jsonl.v1
```

The Azure fixture contains one correct and one deliberately wrong synthetic
classification, with separate numeric and boolean assertions. The Google missing
fixture has an unknown score. Neither `false` nor a missing score is converted to
zero or independent business-task failure. Preview prints mappings, hashes,
counts and fixed rejection codes. It prints no inputs, responses or metric names.

For the full workflow, follow [collector setup](../../docs/quickstart.md), configure
an explicit synthetic content policy, then choose **Experiments → Import a qualified
export** in the authenticated dashboard. Choose the exact format, policy, workflow
and provenance reference, and authorize the encrypted upload. Review the frozen
mapping. A purpose-scoped **Read mapping examples** action shows the historical
output, unreviewed expectation and original source metrics. Numeric values,
booleans, missing values, explicit evaluator errors and skips remain distinct.
Metric names and a supplied source model identifier are content: they appear only
in this authorized view and remain outside ordinary dataset metadata.
Ordinary metadata contains at most 25 safe metric examples, the full projected
count and explicit truncation/scope flags. Full retained details stay encrypted;
that metadata sample is not complete task or population coverage.

Review the exact mapping and rights before creating the unreviewed dataset.
Hide examples to clear that content from the view. [Blind review](../review-workflow/README.md)
can produce a separately versioned labeling decision; a cloud score never grants
independent-label status automatically. No import operation executes an evaluator,
follows a URL, invokes a provider or authorizes an experiment.

## Supported projections

| Profile | Input and historical result | Reported metrics |
|---|---|---|
| `bedrock-automated-model-jsonl.v1` | `inputRecord.prompt`, optional unreviewed `referenceResponse`, exactly one text `modelResponses.response` | `automatedEvaluationResult.scores`; optional source model identifier stays encrypted. [AWS wire format](https://docs.aws.amazon.com/bedrock/latest/userguide/model-evaluation-report-s3.html). |
| `azure-ai-evaluation-1.18.10-results.v1` | Classical `rows` envelope, explicit `inputs.query`/`response`/`ground_truth`; optional text context is preserved in query/context JSON | Numeric, boolean or null scalar `outputs.*` fields. Native AOAI grader envelopes require another profile. [SDK results](https://learn.microsoft.com/en-us/azure/foundry-classic/how-to/develop/evaluate-sdk), [pinned producer](https://pypi.org/project/azure-ai-evaluation/1.18.10/). |
| `vertexai-1.153.1-metrics-jsonl.v1` | `EvalResult.metrics_table.to_json(orient="records", lines=True)` with explicit prompt/response/reference columns; optional context is preserved in prompt/context JSON | Pointwise `metric/score` columns, including nulls. Pairwise/conversation/multimodal results need their own adapters. [Pinned result contract](https://raw.githubusercontent.com/googleapis/python-aiplatform/v1.153.1/vertexai/evaluation/_base.py). |

Reported scores retain their serialized decimal values. No common scale, direction
or pass threshold is inferred. Explicit per-evaluator `completed`, `error` or
`skipped` status is preserved; errors/skips suppress stale usable scores. An absent
score without an explicit error remains missing. Error message bodies and unknown
configuration fields are projected out. Repeated inputs retain separate rows but
share an input family, preventing repeated data from creating independent evidence.

Each file must meet the existing 1 MiB UTF-8, JSON uniqueness/depth and finite-number
rules. At most 64 metric records per row and 10,000 per import are accepted.
Aggregate-only files cannot create missing per-case evidence. AWS human-worker/RAG
formats, Azure native grader output items, arbitrary column mappings and other
SDK versions are not qualified by these profiles. No cloud authenticity, invoice
cost, representative coverage, independent outcome truth or real model quality is
established by importing an export.

## Reproduce the actual local SDK fixtures

The SDKs are fixture tools in a separate environment. They are not Cutback runtime
dependencies. The lock includes transitive versions and hashes.

PowerShell:

```powershell
uv venv --python 3.11.18 .cloud-producers
uv pip install --python .cloud-producers/Scripts/python.exe --require-hashes -r examples/cloud-evaluation-imports/producer-requirements.txt
python examples/cloud-evaluation-imports/generate_fixtures.py --producer-python .cloud-producers/Scripts/python.exe --output artifacts/reproduced-cloud-evaluations
```

On macOS/Linux, use `.cloud-producers/bin/python` in the latter two commands.
Choose a new output directory. The launcher supplies an owned temporary home,
strips inherited credentials/configuration and blocks external connections.
Loopback remains available for Python's local event-loop plumbing. Diagnostic
output, run URLs and local paths never enter the exported fixtures.

Azure's actual pinned SDK evaluates a pure local synthetic callable. Google's
actual pinned `EvalResult` exports a synthetic pandas table, including a null
score. No cloud evaluation service or model is invoked. The AWS example is an
independent synthetic instance of the documented wire format, not a live cloud
job result. [Producer identities and fixture hashes](../../docs/evidence/cloud-evaluation-producer-inspection.json)
record that distinction. Tests, artifact and publication gates are in
[progress](../../docs/progress.md).
