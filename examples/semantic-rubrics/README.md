# Independent generated-rubric example

From matching source, run `uv run --locked cutback demo --semantic-rubrics` with Docker running. No key or paid inference is used. The native SDK, authenticated collector, PostgreSQL, encrypted training/proposal store, owner review, reserved target/judge calls and untouched confirmation are actual product components.

Thirty synthetic ticket episodes alternate urgent and normal reference labels. The sample generator proposes a rubric for an owner's fixed requirement. An ordinary candidate passes the fixture; an intentionally unsafe fixture always returns normal and fails urgent tickets. The replies illustrate the lifecycle and rejected changes, not real model intelligence or savings.

In the dashboard, choose **Explore sample → Inspect generated semantic rubrics**. Read the reviewed private requirements with a purpose, then inspect validation and untouched confirmation. The result remains **model proxy comparison only** and does not authorize a source patch. Generation and judge overhead are shown separately.

The independent declared cases are in `cutback.demo_rubrics.sample_cases`. The whole sample is exercised through actual HTTP/PostgreSQL in `tests/end_to_end/test_semantic_rubric_sample.py`; generation, revocation, invalid citations and immutable thresholds are checked in `tests/integration/test_generated_semantic_rubric_workflow.py`. [Full operator sequence and limits](../../docs/semantic-rubrics.md). Stop the demo with Ctrl+C to remove its owned temporary stack.
