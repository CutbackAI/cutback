import React, { useState } from "react";
import {
  API,
  Data,
  Check,
  Editor,
  Field,
  Form,
  Select,
  json,
  list,
  saveFile,
} from "./ui";
import { parseRecords, ImportCase } from "./records";

export function ProjectForm({
  api,
  organizations,
  created,
}: {
  api: API;
  organizations: Data[];
  created: (id: string) => Promise<void>;
}) {
  return (
    <Form
      title="Create project"
      submit="Create project"
      description="A separate project isolates ingestion keys, evidence and access."
      onSubmit={async (data) => {
        const row = await json(api, "/projects", {
          slug: data.slug,
          environment: data.environment,
          ...(data.organization ? { organization_id: data.organization } : {}),
        });
        await created(row.id);
        return "Project created.";
      }}
    >
      <Field
        label="Project name"
        name="slug"
        required
        pattern="[A-Za-z0-9_.-]+"
        maxLength={100}
        placeholder="support-assistant"
      />
      <Field
        label="Initial environment"
        name="environment"
        required
        defaultValue="staging"
        pattern="[A-Za-z0-9_.-]+"
      />
      {organizations.length > 0 && (
        <Select label="Organization" name="organization">
          {organizations.map((row) => (
            <option key={row.id} value={row.id}>
              {row.name}
            </option>
          ))}
        </Select>
      )}
    </Form>
  );
}

export function PolicyForm({
  api,
  base,
  reload,
}: {
  api: API;
  base: string;
  reload: () => void;
}) {
  return (
    <Editor title="Create scoped capture policy">
      <Form
        title="Capture authorization"
        submit="Create policy"
        description="Only authorize content you have permission to collect. Metadata observation does not require content capture."
        onSubmit={async (data) => {
          await json(api, base + "/capture-policies", {
            enabled: data.enabled === "on",
            approval_ref: data.approval,
            rights_basis: data.rights,
            workflows: list(data.workflows),
            environments: list(data.environments),
            allowed_providers: list(data.providers),
            sample_rate: Number(data.sample_rate) / 100,
            max_samples: Number(data.max_samples),
            max_payload_bytes: Number(data.max_bytes),
            retention_days: Number(data.retention),
            expires_at: new Date(
              Date.now() + Number(data.expiry) * 86400000,
            ).toISOString(),
            never_capture_fields: list(data.denied),
          });
          reload();
          return "Policy saved. New capture still requires explicit SDK opt-in.";
        }}
      >
        <Field
          label="Authorization reference"
          name="approval"
          required
          pattern="[A-Za-z0-9_.:/-]+"
          placeholder="security-review-123"
        />
        <Select label="Data rights" name="rights">
          <option value="synthetic">Synthetic examples</option>
          <option value="owner_authorized">Data owner authorized</option>
          <option value="licensed">Licensed for this purpose</option>
        </Select>
        <Field
          label="Allowed workflows"
          name="workflows"
          required
          placeholder="classify, extract"
        />
        <Field
          label="Allowed environments"
          name="environments"
          required
          defaultValue="staging"
        />
        <Field
          label="Allowed providers"
          name="providers"
          required
          defaultValue="openai"
          hint="Comma-separated: openai, anthropic, local"
        />
        <Field
          label="Sampling percent"
          name="sample_rate"
          type="number"
          min="0"
          max="100"
          step="0.01"
          defaultValue="1"
          required
        />
        <Field
          label="Maximum examples"
          name="max_samples"
          type="number"
          min="1"
          max="10000"
          defaultValue="100"
          required
        />
        <Field
          label="Maximum bytes per example"
          name="max_bytes"
          type="number"
          min="1"
          max="512000"
          defaultValue="64000"
          required
        />
        <Field
          label="Retention days"
          name="retention"
          type="number"
          min="1"
          max="30"
          defaultValue="7"
          required
        />
        <Field
          label="Authorization expires in days"
          name="expiry"
          type="number"
          min="1"
          max="30"
          defaultValue="7"
          required
        />
        <Field
          label="Never capture these fields"
          name="denied"
          placeholder="password, address"
          hint="Redaction is a safeguard, not a guarantee of deidentification."
        />
        <Check label="Enable this policy" name="enabled" />
        <Check
          label="I am authorized to approve this data, purpose, vendors and retention."
          name="rights_confirmed"
          required
        />
      </Form>
    </Editor>
  );
}

export function KeyForm({
  api,
  base,
  reload,
}: {
  api: API;
  base: string;
  reload: () => void;
}) {
  const [key, setKey] = useState(""),
    [visible, setVisible] = useState(false),
    [copied, setCopied] = useState(false);
  return (
    <Editor title="Issue project key">
      <Form
        title="New scoped key"
        submit="Issue key"
        onSubmit={async (data) => {
          const row = await json(api, base + "/keys", {
            role: data.role,
            environment: data.environment,
            expires_in_days: Number(data.days),
          });
          setKey(row.token);
          setVisible(false);
          setCopied(false);
          reload();
          return "Key created. Copy it into your secret manager, then clear it.";
        }}
      >
        <Select label="Key role" name="role">
          {["ingest", "viewer", "analyst", "editor", "project_admin"].map(
            (role) => (
              <option key={role}>{role}</option>
            ),
          )}
        </Select>
        <Field
          label="Key environment"
          name="environment"
          defaultValue="staging"
          required
        />
        <Field
          label="Expires in days"
          name="days"
          type="number"
          min="1"
          max="365"
          defaultValue="30"
          required
        />
      </Form>
      {key && (
        <section className="secret-once" aria-label="One-time key">
          <h3>Copy once</h3>
          <p>
            This value will not be returned by key listings. It stays in browser
            memory until cleared.
          </p>
          <label>
            New project key
            <input
              type={visible ? "text" : "password"}
              value={key}
              readOnly
              autoComplete="off"
            />
          </label>
          <div className="actions">
            <button className="secondary" onClick={() => setVisible(!visible)}>
              {visible ? "Hide key" : "Reveal key"}
            </button>
            <button
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(key);
                  setCopied(true);
                } catch {
                  setVisible(true);
                }
              }}
            >
              {copied ? "Copied" : "Copy key"}
            </button>
            <button
              className="secondary"
              onClick={() => {
                setKey("");
                setVisible(false);
              }}
            >
              Clear key
            </button>
          </div>
        </section>
      )}
    </Editor>
  );
}

export function DatasetForm({
  api,
  base,
  reload,
  policies = [],
}: {
  api: API;
  base: string;
  reload: () => void;
  policies?: Data[];
}) {
  const [cases, setCases] = useState<ImportCase[]>([]),
    [fileError, setFileError] = useState(""),
    [filename, setFilename] = useState("");
  return (
    <Editor title="Import authorized examples">
      <Form
        title="Create frozen dataset"
        submit="Import and freeze dataset"
        description="Choose JSON, JSONL or CSV with input and optional gold/reference/group fields. Model outputs become references; they do not become independent gold automatically."
        onSubmit={async (data) => {
          if (!cases.length) throw new Error("CHOOSE_AN_EXAMPLE_FILE");
          await json(api, base + "/datasets", {
            workflow: data.workflow,
            environment: data.environment,
            policy_id: data.policy,
            source_type: data.source,
            provenance_ref: data.provenance,
            independent_labels: data.gold === "on",
            representative: data.representative === "on",
            cases,
          });
          reload();
          return `Imported ${cases.length} examples from ${filename}. Splits and provenance are frozen.`;
        }}
      >
        <label className="field">
          Example file
          <input
            type="file"
            accept=".json,.jsonl,.csv"
            required
            onChange={async (event) => {
              setCases([]);
              setFileError("");
              const file = event.target.files?.[0];
              if (!file) return;
              try {
                if (file.size > 1048576)
                  throw new Error("FILE_EXCEEDS_ONE_MIB");
                setCases(parseRecords(await file.text(), file.name));
                setFilename(file.name);
              } catch (error) {
                setFileError(
                  (error as Error).message.replaceAll("_", " ").toLowerCase(),
                );
              }
            }}
          />
        </label>
        <Field
          label="Dataset workflow"
          name="workflow"
          required
          placeholder="classify"
          pattern="[A-Za-z0-9_.:/-]+"
        />
        <Field
          label="Dataset environment"
          name="environment"
          required
          defaultValue="staging"
        />
        {policies.length ? (
          <Select label="Approved capture policy" name="policy" required>
            {policies
              .filter((p) => p.enabled)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.policy?.approval_ref || p.id}
                </option>
              ))}
          </Select>
        ) : (
          <Field
            label="Approved capture policy ID"
            name="policy"
            required
            hint="Request the policy reference from your project administrator."
          />
        )}
        <Select label="Example source" name="source">
          <option value="synthetic_fixture">Synthetic fixture</option>
          <option value="human_labeled">Human-labeled examples</option>
          <option value="application_records">
            Authorized application records
          </option>
          <option value="sdk_capture">Authorized SDK samples</option>
        </Select>
        <Field
          label="Provenance reference"
          name="provenance"
          required
          pattern="[A-Za-z0-9_.:/-]+"
          placeholder="reviewed-fixture-v1"
        />
        <Check
          label="Gold labels have been independently validated."
          name="gold"
        />
        <Check
          label="These examples represent the intended workflow distribution."
          name="representative"
        />
        <Check
          label="This file is authorized for this policy, purpose and provider scope."
          name="authorized"
          required
        />
        <p className="hint">
          {cases.length
            ? `${cases.length} examples parsed locally; nothing uploaded until you submit.`
            : "No file selected."}
        </p>
        {fileError && (
          <p role="alert" className="form-error">
            {fileError}
          </p>
        )}
      </Form>
    </Editor>
  );
}

export function PlanForm({
  api,
  base,
  reload,
  datasets,
}: {
  api: API;
  base: string;
  reload: () => void;
  datasets: Data[];
}) {
  const [dataset, setDataset] = useState(datasets[0]?.id || "");
  const [strategy, setStrategy] = useState("model_swap");
  const current = datasets.find((row) => row.id === dataset);
  return (
    <Editor title="Plan an experiment">
      <Form
        title="Compare authorized alternatives"
        submit="Freeze proposed plan"
        description="Planning makes no provider calls. A separate hash approval is required before a run can be queued."
        onSubmit={async (data) => {
          const [provider, model] = String(data.model).split("|");
          const baseline = {
            key: "baseline",
            provider,
            model,
            instruction: String(data.instruction || ""),
            max_input_tokens: Number(data.input_bound),
            max_output_tokens: Number(data.output_bound),
          };
          let candidates;
          if (strategy === "prompt_variant")
            candidates = [
              {
                ...baseline,
                key: "prompt-variant",
                instruction: String(data.variant),
              },
            ];
          if (strategy === "prompt_cache") {
            if (provider !== "anthropic")
              throw new Error(
                "PROMPT_CACHE_REQUIRES_ANTHROPIC_MODEL_AND_STATIC_INSTRUCTION",
              );
            candidates = [
              {
                ...baseline,
                key: "prefix-cache",
                prompt_cache: data.cache_ttl,
              },
            ];
          }
          if (strategy === "routing") {
            const cheaper =
              provider === "openai" && model === "gpt-4.1"
                ? "gpt-4.1-mini"
                : provider === "anthropic" && model === "claude-sonnet-4-6"
                  ? "claude-haiku-4-5"
                  : null;
            if (!cheaper)
              throw new Error(
                "ROUTING_REQUIRES_A_SUPPORTED_LARGER_BASELINE_MODEL",
              );
            candidates = [
              {
                key: "bounded-router",
                provider: "router",
                model: "reviewed-size-router-v1",
                max_input_tokens: baseline.max_input_tokens,
                max_output_tokens: baseline.max_output_tokens,
                routes: [
                  {
                    max_input_bytes: Number(data.short_bound),
                    target: { ...baseline, key: "short-input", model: cheaper },
                  },
                  {
                    max_input_bytes: Number(data.long_bound),
                    target: { ...baseline, key: "long-input" },
                  },
                ],
              },
            ];
          }
          const row = await json(api, base + "/experiments/draft", {
            dataset_id: data.dataset,
            baseline,
            ...(candidates ? { candidates } : {}),
            minimum: data.minimum === "" ? null : Number(data.minimum) / 100,
            max_usd: String(data.max_usd),
            max_requests: Number(data.max_requests),
            repeats: Number(data.repeats),
            pilot_samples: Number(data.pilot_samples),
            pilot_minimum:
              data.pilot_minimum === ""
                ? null
                : Number(data.pilot_minimum) / 100,
          });
          reload();
          return `Plan ${row.id} frozen. Review its dataset, price contract and budget before approval.`;
        }}
      >
        <Select
          label="Frozen dataset"
          name="dataset"
          value={dataset}
          onChange={(event) => setDataset(event.target.value)}
          required
        >
          <option value="">Select dataset</option>
          {datasets.map((row) => (
            <option key={row.id} value={row.id}>
              {row.workflow} · {row.provenance?.case_count} examples ·{" "}
              {row.id.slice(0, 8)}
            </option>
          ))}
        </Select>
        <Select label="Baseline model" name="model">
          <option value="openai|gpt-4.1">OpenAI · GPT-4.1</option>
          <option value="openai|gpt-4.1-mini">OpenAI · GPT-4.1 mini</option>
          <option value="anthropic|claude-sonnet-4-6">
            Anthropic · Claude Sonnet 4.6
          </option>
          <option value="anthropic|claude-haiku-4-5">
            Anthropic · Claude Haiku 4.5
          </option>
        </Select>
        <Select
          label="Comparison strategy"
          name="strategy"
          value={strategy}
          onChange={(event) => setStrategy(event.target.value)}
        >
          <option value="model_swap">Cheaper supported model</option>
          <option value="prompt_variant">Reviewed prompt variant</option>
          <option value="prompt_cache">Anthropic static-prefix caching</option>
          <option value="routing">Bounded input-size routing</option>
        </Select>
        <label className="field">
          Baseline static instruction (optional)
          <textarea
            name="instruction"
            rows={3}
            maxLength={16000}
            required={strategy === "prompt_cache"}
            placeholder="Authorized common instruction; stored encrypted"
          />
        </label>
        {strategy === "prompt_variant" && (
          <label className="field">
            Reviewed candidate instruction
            <textarea name="variant" rows={3} maxLength={16000} required />
          </label>
        )}
        {strategy === "prompt_cache" && (
          <Select label="Provider cache retention" name="cache_ttl">
            <option value="anthropic-5m">Ephemeral five minutes</option>
            <option value="anthropic-1h">Ephemeral one hour</option>
          </Select>
        )}
        {strategy === "routing" && (
          <>
            <Field
              label="Short input byte bound"
              name="short_bound"
              type="number"
              min="1"
              max="95904"
              defaultValue="1024"
              required
            />
            <Field
              label="Fallback input byte bound"
              name="long_bound"
              type="number"
              min="1"
              max="95904"
              defaultValue="4096"
              required
            />
            <p className="hint">
              Short inputs use the supported cheaper model; longer inputs use
              the baseline. Input length is a declared rule, not evidence of
              semantic difficulty. Inputs outside both bounds are refused.
            </p>
          </>
        )}
        <Field
          label="Minimum labeled metric percent"
          name="minimum"
          type="number"
          min="0"
          max="100"
          step="0.1"
          hint="Leave empty for technical checks only. Choose a meaningful threshold for your task."
        />
        <Field
          label="Maximum experiment USD"
          name="max_usd"
          type="number"
          min="0"
          max="10000"
          step="0.01"
          defaultValue="0"
          required
        />
        <Field
          label="Maximum requests including judges"
          name="max_requests"
          type="number"
          min="1"
          max="10000"
          defaultValue="100"
          required
        />
        <Field
          label="Repeated evaluations"
          name="repeats"
          type="number"
          min="1"
          max="5"
          defaultValue="1"
          required
        />
        <Field
          label="Input token upper bound"
          name="input_bound"
          type="number"
          min="4097"
          max="100000"
          defaultValue="8192"
          required
        />
        <Field
          label="Maximum output tokens"
          name="output_bound"
          type="number"
          min="16"
          max="16384"
          defaultValue="256"
          required
        />
        <Field
          label="Optional validation pilot groups"
          name="pilot_samples"
          type="number"
          min="0"
          max="100"
          defaultValue="0"
          required
          hint="Zero disables early elimination. Pilots use separate validation groups, then promoted candidates use untouched test groups."
        />
        <Field
          label="Pilot minimum metric percent"
          name="pilot_minimum"
          type="number"
          min="0"
          max="100"
          step="0.1"
          hint="Required when a pilot is enabled; choose a defensible threshold. Pilot calls count against the same budget."
        />
        {current && (
          <p className="hint">
            Task hint: {current.provenance?.task_inference?.kind || "unknown"}.{" "}
            {current.provenance?.independent_labels
              ? "Independent labels declared."
              : "No independent labels declared."}{" "}
            This hint needs review.
          </p>
        )}
      </Form>
    </Editor>
  );
}

export function RateForm({
  api,
  base,
  reload,
}: {
  api: API;
  base: string;
  reload: () => void;
}) {
  return (
    <Editor title="Add effective-dated contract rate">
      <Form
        title="Project rate override"
        submit="Save rate"
        description="USD per million tokens. Only exact provider/model/region/tier matches apply; previous event valuations remain unchanged."
        onSubmit={async (data) => {
          await json(api, base + "/prices/overrides", {
            provider: data.provider,
            model: data.model,
            region: String(data.region || "") || null,
            service_tier: String(data.tier || "") || null,
            input: String(data.input),
            output: String(data.output),
            cache_read: String(data.cache_read),
            cache_write:
              data.cache_write === "" ? null : String(data.cache_write),
            cache_write_1h:
              data.cache_hour === "" ? null : String(data.cache_hour),
            valid_from: new Date(String(data.starts)).toISOString(),
            valid_to: data.ends
              ? new Date(String(data.ends)).toISOString()
              : null,
          });
          reload();
          return "Contract rate recorded.";
        }}
      >
        <Select label="Rate provider" name="provider">
          <option>openai</option>
          <option>anthropic</option>
        </Select>
        <Field label="Exact model ID" name="model" required />
        <Field label="Region (optional)" name="region" />
        <Field label="Service tier (optional)" name="tier" />
        {[
          ["input", "Uncached input rate"],
          ["output", "Output rate"],
          ["cache_read", "Cache read rate"],
        ].map(([name, label]) => (
          <Field
            key={name}
            label={label}
            name={name}
            type="number"
            min="0"
            step="any"
            required
            defaultValue="0"
          />
        ))}
        <Field
          label="Cache write rate (optional)"
          name="cache_write"
          type="number"
          min="0"
          step="any"
        />
        <Field
          label="One-hour cache write rate (optional)"
          name="cache_hour"
          type="number"
          min="0"
          step="any"
        />
        <Field
          label="Effective from (local time)"
          name="starts"
          type="datetime-local"
          required
        />
        <Field
          label="Effective until (optional)"
          name="ends"
          type="datetime-local"
        />
      </Form>
    </Editor>
  );
}

export function DataControls({
  api,
  base,
  reload,
  settings,
  slug,
  deleted,
}: {
  api: API;
  base: string;
  reload: () => void;
  settings: Data;
  slug: string;
  deleted: () => Promise<void>;
}) {
  const [confirm, setConfirm] = useState("");
  return (
    <>
      <Editor title="Retention and metadata export">
        <Form
          title="Retention policy"
          submit="Save retention"
          onSubmit={async (data) => {
            await json(
              api,
              base + "/settings",
              {
                raw_days: Number(data.raw),
                aggregate_days: Number(data.daily),
              },
              "PUT",
            );
            reload();
            return "Retention saved for the next maintenance cycle.";
          }}
        >
          <Field
            label="Raw event retention days"
            name="raw"
            type="number"
            min="1"
            max="395"
            defaultValue={settings.raw_days || 30}
            required
          />
          <Field
            label="Daily summary retention days"
            name="daily"
            type="number"
            min="1"
            max="3650"
            defaultValue={settings.aggregate_days || 395}
            required
          />
        </Form>
        <button
          className="secondary"
          onClick={async () => {
            const text = await api(base + "/export");
            saveFile(text, "cutback-metadata.ndjson", "application/x-ndjson");
          }}
        >
          Download project metadata
        </button>
        <p className="hint">
          Export excludes raw examples and credentials. Backup and
          version-history retention are managed separately.
        </p>
      </Editor>
      <Editor title="Delete this project">
        <Form
          title="Permanent project deletion"
          danger
          submit="Delete project and its stored evidence"
          description="Deletes project metadata and registered encrypted artifacts. Finish/cancel running work first. Backups follow your operator policy."
          onSubmit={async (data) => {
            if (data.slug !== slug)
              throw new Error("TYPE_THE_EXACT_PROJECT_NAME");
            await api(base + "?confirm_slug=" + encodeURIComponent(slug), {
              method: "DELETE",
            });
            await deleted();
            return "Project deleted.";
          }}
        >
          <Field
            label={`Type ${slug} to confirm deletion`}
            name="slug"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            required
          />
          <Check
            label="I authorize permanent deletion of this project and its evidence."
            name="authorized"
            required
          />
        </Form>
      </Editor>
    </>
  );
}
