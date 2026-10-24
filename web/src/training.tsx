import React, { useState } from "react";
import {
  API,
  Data,
  Check,
  Editor,
  Field,
  Form,
  Select,
  Tag,
  json,
  money,
  saveFile,
  explain,
} from "./ui";

export function Training({
  api,
  base,
  data,
  reload,
  admin,
}: {
  api: API;
  base: string;
  data: Data;
  reload: () => void;
  admin: boolean;
}) {
  const [device, setDevice] = useState("cpu"),
    [approval, setApproval] = useState(""),
    [error, setError] = useState("");
  const config = data.config,
    rate =
      device === "cpu"
        ? config.cpu_hourly_upper_usd
        : config.cuda_hourly_upper_usd;
  async function act(path: string, body: unknown) {
    try {
      await json(api, path, body);
      reload();
    } catch (e) {
      setError(explain(e));
    }
  }
  return (
    <>
      <p className="notice">
        Training is optional and disabled by default. It requires separate
        training rights, an operator-registered local safetensors model,
        explicit compute rates, and exact hash approval. A trained adapter can
        memorize private data. Training loss does not verify business quality.
      </p>
      <p>
        Worker execution{" "}
        <Tag>{config.execution_enabled ? "Enabled" : "Disabled"}</Tag> ·
        registered models {config.models.length} · CUDA{" "}
        <Tag>{config.cuda_enabled ? "Operator enabled" : "Disabled"}</Tag>
      </p>
      {!config.models.length ? (
        <p className="empty">
          No local base models registered. Configure the isolated offline
          training runtime and a model checksum using the self-hosted training
          guide.
        </p>
      ) : (
        <Editor title="Propose local training">
          <Form
            title="Freeze training rights and compute budget"
            submit="Propose training job"
            onSubmit={async (values) => {
              const model = config.models.find(
                  (m: Data) => m.ref === values.model,
                ),
                dataset = data.datasets.find(
                  (d: Data) => d.id === values.dataset,
                );
              if (!model || !dataset || rate === null)
                throw new Error(
                  "OPERATOR_MODEL_DATASET_AND_COMPUTE_RATE_REQUIRED",
                );
              await json(api, base + "/training", {
                dataset_id: dataset.id,
                specification: {
                  dataset_hash: dataset.hash,
                  base_model_ref: model.ref,
                  base_model_sha256: model.sha256,
                  training_rights_ref: values.rights,
                  trained_model_owner: values.owner,
                  training_rights_confirmed: true,
                  target_source: values.source,
                  teacher_terms_ref: values.terms || null,
                  device,
                  max_gpu_hours: String(values.gpu || "0"),
                  max_wall_seconds: Number(values.wall),
                  max_usd: String(values.usd),
                  hourly_compute_upper_usd: String(rate),
                  max_steps: Number(values.steps),
                  max_sequence_tokens: Number(values.sequence),
                  lora_rank: Number(values.rank),
                  retention_days: Number(values.retention),
                },
              });
              reload();
              return "Training proposal frozen. Review rights, splits, model checksum and maximum reservation before approval.";
            }}
          >
            <Select label="Training dataset" name="dataset" required>
              {data.datasets.map((d: Data) => (
                <option key={d.id} value={d.id}>
                  {d.workflow} · {d.id.slice(0, 8)}
                </option>
              ))}
            </Select>
            <Select label="Registered base model" name="model" required>
              {config.models.map((m: Data) => (
                <option key={m.ref} value={m.ref}>
                  {m.ref}
                </option>
              ))}
            </Select>
            <Field
              label="Training rights approval reference"
              name="rights"
              required
            />
            <Field
              label="Trained model owner reference"
              name="owner"
              required
            />
            <Select label="Training targets" name="source">
              <option value="gold">Authorized gold labels</option>
              <option value="reference">
                Teacher references with training terms approval
              </option>
            </Select>
            <Field
              label="Teacher training terms reference"
              name="terms"
              hint="Required when using teacher references. General content access does not grant training rights."
            />
            <Select
              label="Training compute"
              name="device"
              value={device}
              onChange={(e) => setDevice(e.target.value)}
            >
              <option value="cpu">Local CPU</option>
              {config.cuda_enabled && (
                <option value="cuda">
                  One explicitly assigned CUDA device
                </option>
              )}
            </Select>
            <Field
              label="Maximum GPU hours"
              name="gpu"
              type="number"
              min="0"
              max="100"
              step="any"
              defaultValue="0"
              required
            />
            <Field
              label="Maximum training wall seconds"
              name="wall"
              type="number"
              min="10"
              max="3600"
              defaultValue="300"
              required
            />
            <Field
              label="Maximum training USD"
              name="usd"
              type="number"
              min="0"
              max="10000"
              step="any"
              defaultValue="0"
              required
            />
            <p className="hint">
              Operator compute bound {money(rate)} / hour; reservation includes
              ten seconds of termination grace. Provider invoices and setup
              hosting overhead remain unknown.
            </p>
            <Field
              label="Maximum optimizer steps"
              name="steps"
              type="number"
              min="1"
              max="10000"
              defaultValue="100"
              required
            />
            <Field
              label="Maximum sequence tokens"
              name="sequence"
              type="number"
              min="16"
              max="4096"
              defaultValue="512"
              required
            />
            <Field
              label="LoRA rank"
              name="rank"
              type="number"
              min="1"
              max="64"
              defaultValue="8"
              required
            />
            <Field
              label="Trained artifact retention days"
              name="retention"
              type="number"
              min="1"
              max="30"
              defaultValue="1"
              required
            />
            <Check
              label="I have permission to train on these examples and retain these model artifacts."
              name="rights_confirmed"
              required
            />
          </Form>
        </Editor>
      )}
      {admin && (
        <Field
          label="Training budget approval reference"
          name="approval"
          value={approval}
          onChange={(e) => setApproval(e.target.value)}
        />
      )}
      <h2>Training jobs</h2>
      {!data.jobs.length && (
        <p className="empty">
          No training jobs. Model evaluation does not require fine-tuning.
        </p>
      )}
      {data.jobs.map((job: Data) => (
        <article className="row" key={job.id}>
          <h3>
            {job.specification.base_model_ref} <Tag>{job.status}</Tag>
          </h3>
          <p>
            Reserved compute bound {money(job.reserved_usd)} · semantic quality{" "}
            {job.results.semantic_quality || "NOT VERIFIED"}
          </p>
          <details>
            <summary>
              Rights, frozen contract, artifact hashes and compute evidence
            </summary>
            <pre>{JSON.stringify(job, null, 2)}</pre>
          </details>
          {admin && job.status === "DRAFT" && (
            <button
              disabled={!approval}
              onClick={() =>
                void act(base + `/training/${job.id}/approve`, {
                  approval_hash: job.approval_hash,
                  approval_ref: approval,
                })
              }
            >
              Approve and queue this training budget
            </button>
          )}
          {admin && ["DRAFT", "QUEUED", "RUNNING"].includes(job.status) && (
            <button
              className="secondary"
              onClick={() => void act(base + `/training/${job.id}/cancel`, {})}
            >
              Cancel training
            </button>
          )}
          {admin &&
            job.status === "COMPLETED" &&
            job.results.artifacts?.map((artifact: Data, index: number) => (
              <button
                className="secondary"
                key={artifact.name}
                onClick={async () => {
                  try {
                    const blob = await json(
                      api,
                      base + `/training/${job.id}/artifacts/${index}`,
                      { purpose: "authorized-operator-download" },
                    );
                    saveFile(blob, artifact.name, "application/octet-stream");
                  } catch (e) {
                    setError(explain(e));
                  }
                }}
              >
                Download encrypted-store artifact: {artifact.name}
              </button>
            ))}
          {job.results.heldout_evaluation_required && (
            <p className="hint">
              Export this adapter only into an authorized local inference
              environment, then create a separate held-out evaluation before
              recommending a model change.
            </p>
          )}
        </article>
      ))}
      {error && <p role="alert">{error}</p>}
    </>
  );
}
