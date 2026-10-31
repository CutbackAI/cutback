# Generate and review development cases

From the repository checkout, with Python and Docker available:

```shell
python -m pip install -e ".[server,openai]"
cutback demo . --generated-scenarios
```

This independent synthetic support application demonstrates the complete local generation, review, import and evaluation loop. The native SDK uses an owned mock HTTP transport, with no paid provider calls. Open **Experiments → Generated development scenarios** in the printed local dashboard address. Expand **Expand training scenarios**, choose **Owned synthetic demonstration only** as the execution mode, then freeze a scenario version, approve its budget, review the encrypted proposal and import development cases. The sample worker runs approved fixture proposals in this sample project only; it does not dispatch configured-provider plans.

The sample preserves category/priority assertions while adding a concise-response user context and mapping it to an owner-declared goal. It includes the original source anchors in the derived dataset so variants cannot acquire fresh holdout identity. The imported dataset has a validation split only. A separate synthetic target budget runs the actual SDK/collector/database comparison; these results are demonstration evidence, not customer savings or semantic accuracy.

The runnable fixture is [`cutback.demo_scenarios`](../../src/cutback/demo_scenarios.py); the HTTP/native/database and authenticated-browser acceptance is [`test_generated_scenarios_pipeline.py`](../../tests/end_to_end/test_generated_scenarios_pipeline.py). [Contracts, agent commands and limits](../../docs/generated-scenarios.md) explain how to use your own permitted training examples without changing the original tools, gold, permissions or quality requirements. Reactive simulated users and independent labels remain separate capabilities.
