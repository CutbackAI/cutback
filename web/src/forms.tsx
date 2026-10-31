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
const SecurityAcceptanceFields = React.lazy(() =>
  import("./security-suites").then((module) => ({
    default: module.SecurityAcceptanceFields,
  })),
);

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
          if (
            (data.profile_authorized === "on") !==
            (data.profile_immutable === "on")
          ) {
            throw new Error(
              "NATIVE_PROFILE_BOTH_EXPLICIT_AUTHORIZATIONS_REQUIRED",
            );
          }
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
            ...(data.profile_authorized === "on" &&
            data.profile_immutable === "on"
              ? {
                  native_request_profile: {
                    freshness_ref: data.profile_freshness,
                    prospective_ttl_seconds: Number(data.profile_ttl),
                    fingerprint_capture_authorized: true,
                    immutable_or_safely_invalidated: true,
                  },
                }
              : {}),
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
        <fieldset>
          <legend>Optional encrypted request reuse analysis</legend>
          <p>
            Fingerprints remain in encrypted capture storage. Each profiled
            example consumes two object slots. Configure a separate private SDK
            profile key; do not enter it here. Field exclusions disable
            profiling.
          </p>
          <Check
            label="I explicitly authorize encrypted request fingerprint capture."
            name="profile_authorized"
          />
          <Check
            label="The profiled context is immutable or safely invalidated."
            name="profile_immutable"
          />
          <Field
            label="Profile freshness reference"
            name="profile_freshness"
            placeholder="approved-context-version"
          />
          <Field
            label="Prospective cache TTL in seconds"
            name="profile_ttl"
            type="number"
            min="1"
            max="2592000"
            defaultValue="300"
          />
        </fieldset>
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
          {[
            "ingest",
            "viewer",
            "analyst",
            "editor",
            "project_admin",
            "prompt_reader",
          ].map((role) => (
            <option key={role}>{role}</option>
          ))}
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
  const [criterionCount, setCriterionCount] = useState(0);
  const [semanticEnabled, setSemanticEnabled] = useState(false);
  const [reactiveEnabled, setReactiveEnabled] = useState(false);
  const [semanticCount, setSemanticCount] = useState(1);
  const [toolGuards, setToolGuards] = useState(false);
  const current = datasets.find((row) => row.id === dataset);
  const episode =
    current?.provenance?.task_inference?.suggested_metric ===
    "episode_contract";
  const isolated =
    current?.provenance?.task_inference?.kind === "isolated_record_episode";
  const securitySuite = current?.provenance?.security_suite;
  return (
    <Editor title="Plan an experiment">
      <Form
        title="Compare authorized alternatives"
        submit="Freeze proposed plan"
        description="This form prepares standalone evaluations. Use Agent workflow to preserve the application's request and source binding for a change proposal. Planning makes zero provider calls; an exact hash approval is required to queue one run."
        onSubmit={async (data) => {
          const [provider, model] = String(data.model).split("|");
          let toolGuardBinding;
          if (isolated && toolGuards) {
            const policy = await api(
              `${base}/runtime-guardrails/${String(data.tool_guard_policy)}`,
            );
            if (
              policy.state !== "APPROVED" ||
              policy.execution_authority !== "current"
            )
              throw new Error("GUARDED_TOOL_CURRENT_APPROVAL_REQUIRED");
            toolGuardBinding = policy.binding;
          }
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
            name: String(data.name || "").trim() || null,
            dataset_id: data.dataset,
            baseline,
            ...(candidates ? { candidates } : {}),
            minimum:
              episode || data.minimum === ""
                ? null
                : Number(data.minimum) / 100,
            max_usd: String(data.max_usd),
            max_requests: Number(data.max_requests),
            repeats: Number(data.repeats),
            retain_case_outputs: !!data.retain_outputs,
            ...(securitySuite
              ? {
                  security_suite: {
                    suite: securitySuite,
                    minimum_families: Number(data.security_min_families),
                    maximum_attack_family_rate:
                      Number(data.security_max_attack) / 100,
                    minimum_benign_utility:
                      Number(data.security_min_benign) / 100,
                    minimum_attacked_utility:
                      Number(data.security_min_attacked) / 100,
                    maximum_benign_utility_drop:
                      Number(data.security_max_drop) / 100,
                  },
                }
              : {}),
            ...(episode
              ? { episode_max_turns: Number(data.episode_turns) }
              : {}),
            ...(episode && reactiveEnabled && !isolated
              ? {
                  user_simulation: {
                    model: {
                      key: "simulated-user",
                      provider: "openai",
                      model: String(data.simulator_model),
                      max_input_tokens: Number(data.simulator_input),
                      max_output_tokens: Number(data.simulator_output),
                    },
                    persona: String(data.simulator_persona),
                    max_turns: Number(data.simulator_turns),
                    max_transcript_bytes: Number(data.simulator_transcript),
                  },
                }
              : {}),
            ...(isolated
              ? {
                  isolated_tools: {
                    version: toolGuardBinding
                      ? "isolated-record-guarded-tools-v1"
                      : securitySuite
                        ? "isolated-record-security-tools-v1"
                        : "isolated-record-tools-v1",
                    ...(toolGuardBinding
                      ? { runtime_guardrails: toolGuardBinding }
                      : {}),
                    project_id: base.split("/").pop(),
                    principal_id: String(data.tool_principal),
                    broker_id: String(data.tool_broker),
                    environment: current?.provenance?.environment || "staging",
                    image_id: String(data.tool_image),
                    max_sessions: Number(data.tool_sessions),
                    max_total_tool_calls: Number(data.tool_calls),
                    max_wall_seconds: Number(data.tool_wall),
                    max_result_bytes: Number(data.tool_result),
                    content_use_approved: !!data.tool_content_consent,
                  },
                }
              : {}),
            ...(episode && semanticEnabled && !isolated
              ? {
                  episode_judge: {
                    key: "episode-judge",
                    provider: "openai",
                    model: String(data.judge_model),
                    max_input_tokens: Number(data.judge_input),
                    max_output_tokens: Number(data.judge_output),
                  },
                  episode_judging: {
                    criteria: Array.from({ length: semanticCount }, (_, i) => ({
                      key: String(data[`semantic_kind_${i}`]).replaceAll(
                        "_",
                        "-",
                      ),
                      kind: String(data[`semantic_kind_${i}`]),
                      judge_rubric: String(data[`semantic_rubric_${i}`]),
                      minimum: Number(data[`semantic_minimum_${i}`]) / 100,
                    })),
                  },
                }
              : {}),
            criteria: Array.from(
              { length: episode ? 0 : criterionCount },
              (_, i) => ({
                key: String(data[`criterion_key_${i}`]),
                metric: {
                  kind: String(data[`criterion_kind_${i}`]),
                  output_field: data[`criterion_field_${i}`] || null,
                  minimum: Number(data[`criterion_min_${i}`]) / 100,
                },
                required: !!data[`criterion_required_${i}`],
                weight: 1,
              }),
            ),
            pilot_samples: episode ? 0 : Number(data.pilot_samples),
            pilot_minimum:
              episode || data.pilot_minimum === ""
                ? null
                : Number(data.pilot_minimum) / 100,
          });
          reload();
          return `Plan ${row.id} frozen. Review its dataset, price contract and budget before approval.`;
        }}
      >
        <Field
          label="Experiment name"
          name="name"
          maxLength={100}
          pattern="[A-Za-z0-9][A-Za-z0-9 .:/_-]*"
          placeholder="Support triage - compact model"
          hint="Describe the decision you want this comparison to answer."
        />
        <Select
          label="Frozen dataset"
          name="dataset"
          value={dataset}
          onChange={(event) => {
            setDataset(event.target.value);
            setStrategy("model_swap");
            setSemanticEnabled(false);
            setReactiveEnabled(false);
          }}
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
          <option value="prompt_cache" disabled={episode}>
            Anthropic static-prefix caching
          </option>
          <option value="routing" disabled={episode}>
            Bounded input-size routing
          </option>
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
        {!episode && (
          <>
            <Field
              label="Minimum labeled metric percent"
              name="minimum"
              type="number"
              min="0"
              max="100"
              step="0.1"
              hint="Leave empty for technical checks only. Choose a meaningful threshold for your task."
            />
          </>
        )}
        {episode && (
          <fieldset>
            <legend>Agent and RAG episode evaluation</legend>
            {securitySuite && (
              <React.Suspense fallback={<p>Loading security acceptance…</p>}>
                <SecurityAcceptanceFields securitySuite={securitySuite} />
              </React.Suspense>
            )}
            <p>
              {isolated
                ? "Each case uses actual scoped SQL records in the separately operated isolated tool service. Record permissions and state transitions come from this frozen dataset. Production applications are not modified."
                : "Every case runs its declared synthetic conversation and tool fixtures. No real tool actions are performed."}
            </p>
            <Field
              label="Maximum native turns per case"
              name="episode_turns"
              type="number"
              min="1"
              max="32"
              defaultValue="8"
              required
            />
            {isolated && (
              <fieldset>
                <legend>Approved isolated tool service</legend>
                <p>
                  Use the identity supplied by the operator. The service must
                  match this project, record owner, environment and pinned
                  image. It receives no model or database credentials.
                </p>
                <Field
                  label="Tool service ID"
                  name="tool_broker"
                  required
                  pattern="[a-fA-F0-9-]{36}"
                />
                <Field
                  label="Authorized record owner ID"
                  name="tool_principal"
                  required
                  pattern="[a-fA-F0-9-]{36}"
                />
                <Field
                  label="Approved container image digest"
                  name="tool_image"
                  required
                  pattern="sha256:[a-f0-9]{64}"
                />
                <p>
                  Environment: {current?.provenance?.environment || "staging"}
                </p>
                <Field
                  label="Maximum isolated sessions for this run"
                  name="tool_sessions"
                  type="number"
                  min="1"
                  max={securitySuite && !toolGuards ? "1000" : "128"}
                  defaultValue={securitySuite ? "128" : "32"}
                  required
                />
                <Field
                  label="Maximum total tool operations"
                  name="tool_calls"
                  type="number"
                  min="1"
                  max={securitySuite && !toolGuards ? "100000" : "4096"}
                  defaultValue={securitySuite ? "4096" : "512"}
                  required
                />
                <Field
                  label="Maximum seconds per isolated session"
                  name="tool_wall"
                  type="number"
                  min="1"
                  max="120"
                  defaultValue="60"
                  required
                />
                <Field
                  label="Maximum bytes per tool result"
                  name="tool_result"
                  type="number"
                  min="256"
                  max="64000"
                  defaultValue="16000"
                  required
                />
                <label className="check">
                  <input type="checkbox" name="tool_content_consent" required />
                  I authorize the selected records to be used in this isolated
                  tool service.
                </label>
                <p className="hint">
                  A lost operation response is not retried. Final state and
                  confirmed cleanup are required for a passing task. Tool and
                  complete task costs remain unknown.
                </p>
                <>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={toolGuards}
                      onChange={(event) => setToolGuards(event.target.checked)}
                    />
                    Require approved checks before each tool call and before
                    releasing its response.
                  </label>
                  {toolGuards && (
                    <>
                      <Field
                        label="Approved tool guard policy ID"
                        name="tool_guard_policy"
                        required
                        pattern="[a-fA-F0-9-]{36}"
                      />
                      <p>
                        Register and approve a policy in Runtime guardrails with
                        exactly tool_input and tool_output stages. It must match
                        this record owner, project, environment and workflow.
                        The operator must configure its separate checker on the
                        worker. A blocked response does not undo a recorded
                        write. Model prompts and replies are outside this tool
                        policy.
                      </p>
                    </>
                  )}
                </>
              </fieldset>
            )}
            {!isolated && (
              <>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={reactiveEnabled}
                    onChange={(event) => {
                      setReactiveEnabled(event.target.checked);
                      if (event.target.checked) setSemanticEnabled(false);
                    }}
                  />
                  Add a reactive simulated user
                </label>
                {reactiveEnabled && (
                  <fieldset>
                    <legend>Reactive simulated user</legend>
                    <p>
                      After the scripted conversation, this model reacts to the
                      observed replies and returned tool data. Each user-model
                      call consumes a separate request and money reservation
                      inside the same run budget. Generated messages cannot
                      grant permissions. These trajectories do not represent
                      real human behavior.
                    </p>
                    <Select label="User simulator model" name="simulator_model">
                      <option value="gpt-4.1-mini">
                        OpenAI · GPT-4.1 mini
                      </option>
                      <option value="gpt-4.1">OpenAI · GPT-4.1</option>
                    </Select>
                    <Select
                      label="Simulated user persona"
                      name="simulator_persona"
                    >
                      <option value="curious">Curious</option>
                      <option value="skeptical">Skeptical</option>
                      <option value="hurried">Hurried</option>
                      <option value="needs_clarification">
                        Needs clarification
                      </option>
                    </Select>
                    <Field
                      label="Maximum reactive user turns"
                      name="simulator_turns"
                      type="number"
                      min="1"
                      max="3"
                      defaultValue="2"
                      required
                    />
                    <Field
                      label="Simulator input token bound"
                      name="simulator_input"
                      type="number"
                      min="8192"
                      max="100000"
                      defaultValue="32768"
                      required
                    />
                    <Field
                      label="Simulator output token bound"
                      name="simulator_output"
                      type="number"
                      min="16"
                      max="16384"
                      defaultValue="512"
                      required
                    />
                    <Field
                      label="Maximum observed transcript bytes"
                      name="simulator_transcript"
                      type="number"
                      min="1000"
                      max="32000"
                      defaultValue="16000"
                      required
                    />
                    <p className="hint">
                      The original case and plan turn limits still apply. Gold
                      answers, hidden fixtures and assertions are excluded from
                      simulator inputs. Compare total evaluation cost and target
                      cost separately.
                    </p>
                  </fieldset>
                )}
                <label className="check">
                  <input
                    type="checkbox"
                    checked={semanticEnabled}
                    onChange={(event) => {
                      setSemanticEnabled(event.target.checked);
                      if (event.target.checked) setReactiveEnabled(false);
                    }}
                  />
                  Add a semantic model judge
                </label>
              </>
            )}
            {semanticEnabled && !isolated && (
              <>
                <p className="hint">
                  This adds one separately budgeted OpenAI request per completed
                  case and model. The dataset policy must permit OpenAI. Scores
                  are model proxies, never verified customer quality. Invalid
                  citations and missing evidence remain unknown.
                </p>
                {Array.from({ length: semanticCount }, (_, i) => (
                  <fieldset key={i}>
                    <legend>Semantic criterion {i + 1}</legend>
                    <Select
                      label={`Semantic check ${i + 1}`}
                      name={`semantic_kind_${i}`}
                      defaultValue={
                        [
                          "answer_correctness",
                          "retrieval_groundedness",
                          "conversation_constraints",
                        ][i]
                      }
                    >
                      <option value="answer_correctness">
                        Answer agreement with a declared reference
                      </option>
                      <option value="retrieval_groundedness">
                        Answer grounded in actually returned documents
                      </option>
                      <option value="conversation_constraints">
                        Compliance with observed conversation constraints
                      </option>
                    </Select>
                    <label className="field">
                      Semantic evaluation rubric {i + 1}
                      <textarea
                        name={`semantic_rubric_${i}`}
                        rows={4}
                        maxLength={8000}
                        required
                        placeholder="Describe the task-specific rule to assess. Stored encrypted with the frozen plan."
                      />
                    </label>
                    <Field
                      label={`Minimum semantic score percent ${i + 1}`}
                      name={`semantic_minimum_${i}`}
                      type="number"
                      min="0"
                      max="100"
                      step="0.1"
                      defaultValue="100"
                      required
                    />
                  </fieldset>
                ))}
                <button
                  type="button"
                  disabled={semanticCount >= 3}
                  onClick={() => setSemanticCount(semanticCount + 1)}
                >
                  Add semantic criterion
                </button>
                {semanticCount > 1 && (
                  <button
                    type="button"
                    onClick={() => setSemanticCount(semanticCount - 1)}
                  >
                    Remove last semantic criterion
                  </button>
                )}
                <Select label="Episode judge model" name="judge_model">
                  <option value="gpt-4.1-mini">OpenAI · GPT-4.1 mini</option>
                  <option value="gpt-4.1">OpenAI · GPT-4.1</option>
                </Select>
                <Field
                  label="Judge input token upper bound"
                  name="judge_input"
                  type="number"
                  min="4097"
                  max="100000"
                  defaultValue="64000"
                  required
                />
                <Field
                  label="Judge output token upper bound"
                  name="judge_output"
                  type="number"
                  min="16"
                  max="16384"
                  defaultValue="2048"
                  required
                />
              </>
            )}
          </fieldset>
        )}
        {!episode && (
          <>
            <fieldset>
              <legend>Additional quality criteria</legend>
              <p>
                Every required criterion must pass. A good average cannot hide a
                required failure.
              </p>
              {Array.from({ length: criterionCount }, (_, i) => (
                <fieldset key={i}>
                  <legend>Criterion {i + 1}</legend>
                  <Field
                    label={`Criterion ${i + 1} name`}
                    name={`criterion_key_${i}`}
                    required
                    pattern="[A-Za-z0-9_.:/-]+"
                  />
                  <Select
                    label={`Criterion ${i + 1} metric`}
                    name={`criterion_kind_${i}`}
                  >
                    <option value="exact_label">Exact expected label</option>
                    <option value="nonempty">
                      Nonempty response (technical only)
                    </option>
                  </Select>
                  <Field
                    label={`Criterion ${i + 1} JSON field (optional)`}
                    name={`criterion_field_${i}`}
                    pattern="[A-Za-z_][A-Za-z0-9_]*"
                  />
                  <Field
                    label={`Criterion ${i + 1} minimum percent`}
                    name={`criterion_min_${i}`}
                    type="number"
                    min="0"
                    max="100"
                    defaultValue="95"
                    required
                  />
                  <Check
                    label={`Criterion ${i + 1} is required`}
                    name={`criterion_required_${i}`}
                  />
                </fieldset>
              ))}
              <button
                type="button"
                disabled={criterionCount >= 8}
                onClick={() => setCriterionCount(criterionCount + 1)}
              >
                Add quality criterion
              </button>
              {criterionCount > 0 && (
                <button
                  type="button"
                  onClick={() => setCriterionCount(criterionCount - 1)}
                >
                  Remove last criterion
                </button>
              )}
            </fieldset>
          </>
        )}
        <Check
          name="retain_outputs"
          label="Retain encrypted case outputs under the dataset's active rights policy, quota and expiry."
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
          label="Maximum requests including judges and simulated users"
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
        {!episode && (
          <>
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
          </>
        )}
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
  const [provider, setProvider] = useState("openai");
  const [modern, setModern] = useState(false);
  return (
    <Editor title="Add effective-dated contract rate">
      <Form
        title="Project rate override"
        submit="Save rate"
        description="USD per million tokens. Only exact provider/model/region/tier matches apply; previous event valuations remain unchanged."
        onSubmit={async (data) => {
          await json(api, base + "/prices/overrides", {
            provider,
            model: data.model,
            region: String(data.region || "") || null,
            service_tier: String(data.tier || "") || null,
            input: String(data.input),
            output: String(data.output),
            cache_read: String(data.cache_read),
            cache_write:
              modern || !data.cache_write ? null : String(data.cache_write),
            cache_write_1h:
              modern || !data.cache_hour ? null : String(data.cache_hour),
            ...(modern
              ? {
                  input_token_contract: "openai-inclusive-cache-writes-v2",
                  cache_write_30m: String(data.cache_30m),
                  cache_write_12h: String(data.cache_12h),
                  long_context_threshold: Number(data.context_threshold),
                  long_context_input_multiplier: String(data.context_input),
                  long_context_output_multiplier: String(data.context_output),
                }
              : {}),
            valid_from: new Date(String(data.starts)).toISOString(),
            valid_to: data.ends
              ? new Date(String(data.ends)).toISOString()
              : null,
          });
          reload();
          return "Contract rate recorded.";
        }}
      >
        <Select
          label="Rate provider"
          name="provider"
          value={provider}
          onChange={(event) => {
            setProvider(event.target.value);
            if (event.target.value !== "openai") setModern(false);
          }}
        >
          <option>openai</option>
          <option>anthropic</option>
        </Select>
        {provider === "openai" && (
          <Select
            label="Input token contract"
            name="token_contract"
            value={modern ? "modern" : "legacy"}
            onChange={(event) => setModern(event.target.value === "modern")}
          >
            <option value="legacy">Legacy input includes cached reads</option>
            <option value="modern">
              OpenAI input includes disjoint cache reads and writes
            </option>
          </Select>
        )}
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
          />
        ))}
        {modern ? (
          <>
            <Field
              label="30-minute cache write rate"
              name="cache_30m"
              type="number"
              min="0"
              step="any"
              required
            />
            <Field
              label="12-hour cache write rate"
              name="cache_12h"
              type="number"
              min="0"
              step="any"
              required
            />
            <Field
              label="Long-context threshold tokens"
              name="context_threshold"
              type="number"
              min="1"
              max="100000000"
              defaultValue="272000"
              required
            />
            <Field
              label="Long-context input/cache multiplier"
              name="context_input"
              type="number"
              min="1"
              max="10"
              step="any"
              defaultValue="2"
              required
            />
            <Field
              label="Long-context output multiplier"
              name="context_output"
              type="number"
              min="1"
              max="10"
              step="any"
              defaultValue="1.5"
              required
            />
          </>
        ) : provider === "anthropic" ? (
          <>
            <Field
              label="Five-minute cache write rate (optional)"
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
          </>
        ) : (
          <p className="hint">
            Choose the versioned write contract to declare OpenAI cache-write
            rates.
          </p>
        )}
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
