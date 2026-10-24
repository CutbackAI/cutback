# Customer-owned local LoRA training

The training adapter is an optional local process using Hugging Face Transformers and PEFT. It does not provision GPUs, call a hosted trainer, download a model, push a model to a hub, or enable training from an ordinary scan. The scanner and collector environments do not require Torch.

The executed native acceptance uses Windows/Python 3.13, **Torch 2.14.1+cpu, Transformers 5.20.2, PEFT 0.21.2 and safetensors 0.8.0**. It creates a tiny randomly initialized owned GPT-2 fixture, performs real LoRA optimizer steps on synthetic training examples, encrypts the resulting adapter, downloads it through authenticated HTTP and verifies its checksum. This validates mechanics; it does not establish useful language-model quality, GPU behavior or customer savings.

## Isolated runtime and registered weights

```sh
uv sync --project infra/training --locked
```

The separate lock uses the official CPU wheel index explicitly for Torch; other packages use the normal package index. It targets Python 3.11–3.13 on Windows/Linux. A CUDA runtime is a separately reviewed customer environment. CUDA execution is disabled until `CUTBACK_TRAINING_ALLOW_CUDA=true`; exactly one explicitly assigned visible GPU is supported. No GPU acceptance is claimed by the CPU fixture.

Place a permitted **local** base model and tokenizer in a read-only operator-owned directory. Only bounded JSON, safetensors, tokenizer text/model files are accepted; symlinks and executable/pickle weight files are refused. The loader uses `local_files_only=True`, `trust_remote_code=False` and safetensors. Hash the registered directory:

```sh
cutback training model-hash /approved/read-only/base-model
```

Configure the customer worker with an absolute `CUTBACK_TRAINING_PYTHON` pointing to the isolated runtime, `CUTBACK_TRAINING_MODELS` mapping a reviewed reference to `{"path":"/approved/read-only/base-model","sha256":"<reviewed hash>"}`, and an explicit `CUTBACK_TRAINING_CPU_HOURLY_USD` (or `_CUDA_HOURLY_USD`). A zero rate is an explicit customer compute bound, not an invoice assertion. Register model licenses/ownership before using third-party weights. Directory hashes are checked again before training.

Run the trainer in a dedicated resource-limited container/account with read-only model mounts, private temporary storage, disabled network egress and a single assigned device. The subprocess receives an isolated environment and scratch caches; production/API credentials are not inherited. CPU threads are limited. OS/container limits must bound RAM/VRAM and process resources; Python byte/step/wall limits do not replace cgroups or GPU resource controls.

## Rights, splits, budgets and approval

1. Import a rights-cleared encrypted dataset under a policy permitting local processing. Training requires at least twenty training examples and five separate examples in each validation/test split; existing duplicate/customer group isolation is preserved.
2. Propose training in the dashboard or `cutback training propose <project-uuid> reviewed-training.json`. The request contains a dataset ID and `TrainingSpec`: frozen dataset/model hashes, training-rights reference, trained-model owner, target source, hyperparameters, retention and compute bounds. Teacher references additionally require a specific teacher-training terms reference. General payload collection permission does not grant training rights.
3. Review the returned exact approval hash and maximum reservation. `cutback training approve` requires an administrator and an approval reference. Approval queues one job and reserves the full declared compute upper bound, including ten seconds of termination grace. A request cannot be approved twice.
4. Separately enable `CUTBACK_TRAINING_EXECUTION=true` on the customer worker. `cutback training worker-once` executes one claimed job. This enables local work on customer-owned compute; do not enable paid GPU resources without the owner's approval.

The child receives **training examples only**. It applies target-only loss, rejects examples exceeding the frozen sequence bound, freezes base weights and updates LoRA parameters. Test and validation examples never enter the training subprocess. Training loss is diagnostic and cannot pass a semantic quality gate. Step/wall/retention limits and policy revocation are checked; cancellation kills the subprocess. Leases are durable and a crashed or uncertain operation is never automatically replayed. Reservations are not refunded after failures or cancellation. New work needs a new job and approval.

## Artifacts and evaluation

Only the adapter safetensors and adapter config are retained, in bounded encrypted chunks. Metadata includes hashes, sizes, train/split counts, diagnostic losses, measured process wall/GPU hours, a modeled compute subtotal and unknown invoice/setup overhead. Training artifacts can memorize data: they inherit an explicit expiry, require content-authorized operator download, and are deleted by expiry/project deletion. Project deletion is blocked while training is queued or running.

`cutback training download` requires a purpose, creates a new private file without overwriting an existing one, and verifies the streamed SHA-256. Raw provider/application secrets never appear in CLI arguments. The dashboard offers the same audited download. Place an adapter in an authorized local inference service, then create a **new held-out experiment** comparing the trained model to the frozen baseline before recommending any change. Neither successful training nor reduced training loss verifies production quality or savings.

To reproduce the no-charge native acceptance, set `CUTBACK_TEST_TRAINING_PYTHON` to the pinned CPU runtime and run `scripts/check.py tests/integration/test_training.py`. All data and weights in that test are synthetic and generated locally.

The isolated lock explicitly pins setuptools 84.2.1 after auditing the training dependency graph. Training budgets bound the launched job process including termination grace; an already allocated machine/GPU, idle time, storage, setup and external service invoices remain operator costs, not a promised billing cap.
