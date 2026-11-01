# Portable application checks without model calls

Install the current checkout in a virtual environment. This example needs Git and Python; it does not need PostgreSQL, Docker, provider credentials or network inference. The script creates two new synthetic repositories and refuses to overwrite an existing destination.

```sh
python examples/application-checks/sample.py artifacts/check-example
cutback checks prepare artifacts/check-example/base artifacts/check-example/proposed --commands examples/application-checks/commands.json --output artifacts/check-example/contract.json
cutback checks run artifacts/check-example/contract.json artifacts/check-example/base artifacts/check-example/proposed --execute-local --output artifacts/check-example/receipt.json --junit artifacts/check-example/junit.xml
cutback checks verify artifacts/check-example/contract.json artifacts/check-example/receipt.json artifacts/check-example/base artifacts/check-example/proposed
```

The working-source `checks discover` command can replace the `checks prepare` line above, avoiding a handwritten command file:

```sh
cutback checks discover artifacts/check-example/base artifacts/check-example/proposed --output artifacts/check-example/contract.json --report artifacts/check-example/discovery.json
```

Inspect the proposed commands and discovery limitations, then use the same explicit `checks run` and `checks verify` commands. Discovery does not import `app.py`, collect tests or run package scripts. Source-only availability and publication gates are in [progress](../../docs/progress.md).

Expected: both application checks pass, a source-bound JSON receipt and JUnit report are written outside the two source roots, and verification returns `PASSED_RECORDED_LOCAL_CHECKS`. Neither command output nor inherited provider/admin secrets enter the receipt. Change a tracked file after preparation and the runner rejects it before any check. Commit a deliberate regression in the proposed workspace, prepare a new contract and the proposed check fails with exit status one.

Preparation is parse-only Git/file inspection. `--execute-local` explicitly authorizes the exact declared application commands. The process runner is not an OS sandbox: use reviewed code in an isolated development/CI environment without mounted secrets or live infrastructure access. See [contract scope, bounds and attestation limits](../../docs/application-checks.md).

## Run the same example in a container

The new v2 source path is under qualification. It needs a trusted local Linux
Docker daemon and an already installed image containing the reviewed runtime.
Obtain the exact image ID from your own image inspection; the runner never
pulls, builds or installs dependencies. After the preparation above:

```sh
cutback checks isolate artifacts/check-example/contract.json --image-id sha256:YOUR_64_HEX_IMAGE_ID --python-path /usr/local/bin/python --output artifacts/check-example/isolated-contract.json
cutback checks run artifacts/check-example/isolated-contract.json artifacts/check-example/base artifacts/check-example/proposed --execute-local --output artifacts/check-example/isolated-receipt.json --junit artifacts/check-example/isolated-junit.xml
cutback checks verify artifacts/check-example/isolated-contract.json artifacts/check-example/isolated-receipt.json artifacts/check-example/base artifacts/check-example/proposed
```

Use `/app/.venv/bin/python` for the matching collector image. Both checks should
pass and the receipt should declare `local_linux_container_network_none`.
The application sees only copied tracked files and bounded temporary storage;
ignored files, Git history and host credentials are absent. A deliberately
committed classification regression still fails on the proposed tree. See the
[complete container boundaries](../../docs/application-containers.md) before
using application checks that need writes, network or external services.
