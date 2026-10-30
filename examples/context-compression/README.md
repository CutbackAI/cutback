# A support-policy application with real input compression

This independent synthetic application answers a refund-deadline question from supplied context. `app.py` accepts a caller-supplied native OpenAI client. `compression.json` selects the original context or a qualified extractive transform. The scanner never runs the application.

Use current source; these commands are not in the published dev5 wheel. No provider key, paid inference or cloud account is needed for the demo.

## Run the complete local journey

Install Git, Python 3.11–3.13, uv 0.13.6 and Docker Desktop with Linux containers. From the Cutback checkout:

```sh
uv sync --locked --extra server --extra openai
uv run cutback demo
```

The demo runs the actual collector, PostgreSQL, encrypted content storage, native SDK parser and durable worker using synthetic provider transport. It seeds a context-compression validation, untouched confirmation and reversible source proposal. Open **Compression**, refresh its history and inspect the synthetic runs. The aggressive configuration loses the long answer fact; smaller input alone must not make it eligible. Authorized case review shows the actual compressed input.

To prepare a fresh comparison, select the `synthetic-context-compression` dataset. Use a 1024-byte original context limit, the default 20000 native input ceiling, an exact-answer minimum of 0.9, three independent questions and the sample's explicit CPU assumption of `0.01` USD/hour. Review the displayed budget before approval. The ordinary demo does not leave a real-provider worker running; it cannot borrow provider credentials. Completed seeded runs can be inspected without dispatching anything.

Stop the demo with Ctrl+C when finished. Only its own temporary database/container and encrypted artifacts are removed.

## Inspect the application without a provider call

From this directory in the source environment:

```sh
uv run python app.py
uv run python check_application.py
uv run cutback compression render question.json compression.json --output rendered.json
```

The render command writes the requested content frame and byte counts. It leaves token savings and preserved quality unknown. Do not commit generated content from a real application. The checked-in question, facts and labels here are owned synthetic data.

## Prepare a source-bound comparison in an authorized collector

Copy this directory to a fresh application workspace, initialize and commit its Git tree, then inspect the settings without executing the app:

```sh
cutback compression inspect-config APP_ROOT compression.json --output inspection.json
```

Keep `inspection.json` outside the clean application tree. Import the supplied `questions.jsonl` only into a content policy you have authorized. `prepare_dataset.py` creates the synthetic import body; `prepare_request.py` creates a bounded draft body using that dataset's exact identity and the clean application binding. Both are offline and refuse to overwrite their output.

```sh
python prepare_dataset.py --policy POLICY_UUID --output dataset-import.json
python prepare_request.py --root APP_ROOT --dataset DATASET_UUID --output compression-request.json
cutback compression draft compression-request.json --project PROJECT_UUID --endpoint APPROVED_COLLECTOR --output plan.json
```

The ordinary experiment approval/queue controls govern execution. After validation, `compression confirm` freezes one eligible alternative; it does not approve the new test budget. After passing confirmation, `compression proposal` and `compression review` prepare the exact checked change and rollback. Follow [the full contract](../../docs/context-compression.md) for privacy, independent evidence, CPU assumptions and recovery. These examples do not authorize real provider spend or a production change.
