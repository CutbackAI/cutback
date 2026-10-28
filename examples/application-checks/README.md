# Portable application checks without model calls

Install the current checkout in a virtual environment. This example needs Git and Python; it does not need PostgreSQL, Docker, provider credentials or network inference. The script creates two new synthetic repositories and refuses to overwrite an existing destination.

```sh
python examples/application-checks/sample.py artifacts/check-example
cutback checks prepare artifacts/check-example/base artifacts/check-example/proposed --commands examples/application-checks/commands.json --output artifacts/check-example/contract.json
cutback checks run artifacts/check-example/contract.json artifacts/check-example/base artifacts/check-example/proposed --execute-local --output artifacts/check-example/receipt.json --junit artifacts/check-example/junit.xml
cutback checks verify artifacts/check-example/contract.json artifacts/check-example/receipt.json artifacts/check-example/base artifacts/check-example/proposed
```

Expected: both application checks pass, a source-bound JSON receipt and JUnit report are written outside the two source roots, and verification returns `PASSED_RECORDED_LOCAL_CHECKS`. Neither command output nor inherited provider/admin secrets enter the receipt. Change a tracked file after preparation and the runner rejects it before any check. Commit a deliberate regression in the proposed workspace, prepare a new contract and the proposed check fails with exit status one.

Preparation is parse-only Git/file inspection. `--execute-local` explicitly authorizes the exact declared application commands. The process runner is not an OS sandbox: use reviewed code in an isolated development/CI environment without mounted secrets or live infrastructure access. See [contract scope, bounds and attestation limits](../../docs/application-checks.md).
