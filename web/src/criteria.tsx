import React, { useEffect, useRef, useState } from "react";
import {
  type API,
  type Data,
  Check,
  Field,
  Form,
  Select,
  explain,
  json,
  money,
} from "./ui";

type PreparationIdentity = {
  fingerprint: string;
  request_id: string;
  content_nonce: string;
};
async function preparationIdentity(
  body: Data,
  state: { current: PreparationIdentity | null },
) {
  if (!crypto.subtle || !crypto.randomUUID)
    throw new Error(
      "Use HTTPS or the loopback collector for private preparation.",
    );
  const hash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(body)),
  );
  const fingerprint = Array.from(new Uint8Array(hash), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
  if (state.current?.fingerprint !== fingerprint)
    state.current = {
      fingerprint,
      request_id: crypto.randomUUID(),
      content_nonce: crypto.randomUUID(),
    };
  return state.current;
}

export function ReviewedCriteria({
  api,
  base,
  datasets,
  runs,
  admin,
  analyst,
  reload,
}: {
  api: API;
  base: string;
  datasets: Data[];
  runs: Data[];
  admin: boolean;
  analyst: boolean;
  reload: () => void;
}) {
  const [rows, setRows] = useState<Data[]>([]);
  const [selected, setSelected] = useState<Data | null>(null);
  const [content, setContent] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  const [generationKind, setGenerationKind] = useState("deterministic");
  const [dimensionCount, setDimensionCount] = useState(1);
  const draftIdentity = useRef<PreparationIdentity | null>(null);
  const validationIdentity = useRef<PreparationIdentity | null>(null);
  const confirmationIdentity = useRef<PreparationIdentity | null>(null);
  const semanticSelected =
    selected?.criteria_kind === "episode_semantic_rubrics";
  const generation = useRef(0);
  useEffect(() => {
    draftIdentity.current =
      validationIdentity.current =
      confirmationIdentity.current =
        null;
    setSelected(null);
    setContent(null);
  }, [base]);
  useEffect(() => {
    const request = ++generation.current;
    api(`${base}/criteria?limit=25&offset=${offset}`)
      .then((value) => {
        if (request === generation.current) setRows(value);
      })
      .catch((e) => {
        if (request === generation.current) setError(explain(e));
      });
    return () => {
      generation.current++;
    };
  }, [base, api, offset]);
  async function refresh(selection = selected) {
    const request = ++generation.current;
    const page = await api(`${base}/criteria?limit=25&offset=${offset}`);
    if (request !== generation.current) return;
    setRows(page);
    if (selection) {
      const row = await api(`${base}/criteria/${selection.id}`);
      if (request !== generation.current) return;
      setSelected(row);
      if (
        row.review_approval_id !== selection.review_approval_id ||
        row.state !== selection.state
      )
        setContent(null);
    }
    reload();
  }
  const binding =
    selected?.review_status === "APPROVED"
      ? {
          version: "cutback.reviewed-criteria.v1",
          criteria_id: selected.id,
          review_approval_id: selected.review_approval_id,
          version_sha256: selected.version_sha256,
        }
      : null;
  const path = selected ? `${base}/criteria/${selected.id}` : "";
  return (
    <section aria-label="Reviewed evaluation criteria">
      <h2>Reviewed evaluation criteria</h2>
      <p>
        Describe your task and keep your quality requirements fixed. Cutback can
        propose deterministic checks or episode semantic rubrics from up to
        eight authorized training examples. You review the checks, validate
        candidate models, then confirm one candidate on untouched test groups.
      </p>
      <p className="hint">
        Generation uses one separately approved provider call. A generated check
        is a proposal; owner review does not establish semantic quality or
        independent judge calibration. Test rows and generated labels are never
        used to design these checks.
      </p>
      {error && <p role="alert">{error}</p>}
      <button
        type="button"
        onClick={() => refresh().catch((e) => setError(explain(e)))}
      >
        Refresh criteria
      </button>
      {!rows.length && <p>No criteria versions on this page.</p>}
      {rows.map((row) => (
        <article className="row" key={row.id}>
          <p>
            <code>{row.id}</code> · {row.state} · review {row.review_status}
            <br />
            {row.request_reservations} reserved call(s),{" "}
            {row.known_cost_requests ?? "unknown"} with known cost. Known
            generation subtotal: {money(row.known_spend_subtotal_usd)}.
          </p>
          <button
            type="button"
            onClick={() => {
              generation.current++;
              setSelected(row);
              setContent(null);
              setError("");
            }}
          >
            Inspect criteria {row.id}
          </button>
        </article>
      ))}
      <div className="actions">
        <button
          disabled={!offset}
          onClick={() => {
            setOffset(Math.max(0, offset - 25));
            setSelected(null);
            setContent(null);
          }}
        >
          Previous criteria
        </button>
        <button
          disabled={rows.length < 25}
          onClick={() => {
            setOffset(offset + 25);
            setSelected(null);
            setContent(null);
          }}
        >
          Next criteria
        </button>
      </div>
      {analyst && (
        <details className="editor">
          <summary>Propose checks from training examples</summary>
          <Form
            title="Prepare criteria generation"
            submit="Freeze generation plan"
            description="This step stores an encrypted training snapshot and forecasts one call. It sends no provider request."
            onSubmit={async (data) => {
              const semantic = generationKind === "episode_semantic_rubrics";
              const body = {
                dataset_id: data.dataset,
                requirements: {
                  task_brief: data.brief,
                  minimum: Number(data.minimum),
                  max_regression: Number(data.regression),
                  min_test_samples: Number(data.samples),
                  min_test_groups: Number(data.groups),
                  ...(semantic
                    ? {
                        kind: "episode_semantic_rubrics_v1",
                        dimensions: Array.from(
                          { length: dimensionCount },
                          (_, i) => ({
                            kind: data[`dimension_kind_${i}`],
                            requirement: data[`dimension_requirement_${i}`],
                            minimum: Number(data[`dimension_minimum_${i}`]),
                          }),
                        ),
                        judge: {
                          model: data.judge,
                          provider: "openai",
                          max_input_tokens: Number(data.judge_input),
                          max_output_tokens: Number(data.judge_output),
                        },
                      }
                    : {}),
                },
                generator: {
                  key: "criteria-generator",
                  provider: "openai",
                  model: data.generator,
                  max_input_tokens: 24000,
                  max_output_tokens: semantic ? 4096 : 2048,
                },
                budget: { max_usd: data.budget, max_requests: 1 },
                max_training_examples: Number(data.examples),
              };
              const identity = await preparationIdentity(body, draftIdentity);
              const row = await json(api, `${base}/criteria/drafts`, {
                ...body,
                request_id: identity.request_id,
              });
              setSelected(row);
              setContent(null);
              await refresh(row);
              return "Generation plan frozen. An admin must approve this exact plan before it can run.";
            }}
          >
            <Select
              label="Generated check type"
              name="kind"
              value={generationKind}
              onChange={(event) => setGenerationKind(event.target.value)}
            >
              <option value="deterministic">Deterministic checks</option>
              <option value="episode_semantic_rubrics">
                Episode semantic rubrics
              </option>
            </Select>
            <CriteriaSelect
              label="Training dataset"
              name="dataset"
              required
              options={datasets.map((d) => [d.id, `${d.workflow} · ${d.id}`])}
            />
            <Field
              label="Task and desired behavior"
              name="brief"
              required
              maxLength={4000}
            />
            <CriteriaSelect
              label="Generator model"
              name="generator"
              options={[
                ["gpt-4.1-mini", "gpt-4.1-mini"],
                ["gpt-4.1", "gpt-4.1"],
              ]}
            />
            {generationKind === "episode_semantic_rubrics" && (
              <>
                <p className="hint">
                  Choose an episode dataset with declared reference labels. A
                  rubric evaluates a model proxy using permitted reference,
                  returned retrieval or observed conversation sources. It does
                  not establish independent semantic truth or authorize a source
                  patch.
                </p>
                <Select
                  label="Semantic dimensions"
                  name="dimensions"
                  value={dimensionCount}
                  onChange={(event) =>
                    setDimensionCount(Number(event.target.value))
                  }
                >
                  {[1, 2, 3].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </Select>
                {Array.from({ length: dimensionCount }, (_, i) => (
                  <fieldset key={i} className="editor">
                    <legend>Dimension {i + 1}</legend>
                    <CriteriaSelect
                      label={`Dimension ${i + 1} evidence`}
                      name={`dimension_kind_${i}`}
                      options={[
                        ["answer_correctness", "Reference correctness"],
                        [
                          "retrieval_groundedness",
                          "Returned retrieval evidence",
                        ],
                        [
                          "conversation_constraints",
                          "Observed conversation requirements",
                        ],
                      ]}
                    />
                    <Field
                      label={`Dimension ${i + 1} owner requirement`}
                      name={`dimension_requirement_${i}`}
                      required
                      maxLength={2000}
                    />
                    <Field
                      label={`Dimension ${i + 1} minimum judge score`}
                      name={`dimension_minimum_${i}`}
                      type="number"
                      min="0"
                      max="1"
                      step="0.01"
                      defaultValue="1"
                      required
                    />
                  </fieldset>
                ))}
                <CriteriaSelect
                  label="Semantic judge model"
                  name="judge"
                  options={[
                    ["gpt-4.1-mini", "gpt-4.1-mini"],
                    ["gpt-4.1", "gpt-4.1"],
                  ]}
                />
                <Field
                  label="Judge maximum input tokens"
                  name="judge_input"
                  type="number"
                  min="256"
                  max="262144"
                  defaultValue="64000"
                  required
                />
                <Field
                  label="Judge maximum output tokens"
                  name="judge_output"
                  type="number"
                  min="128"
                  max="8192"
                  defaultValue="2048"
                  required
                />
              </>
            )}
            <Field
              label="Minimum quality score"
              name="minimum"
              type="number"
              min="0"
              max="1"
              step="0.01"
              defaultValue="0.9"
              required
            />
            <Field
              label="Maximum regression"
              name="regression"
              type="number"
              min="0"
              max="1"
              step="0.01"
              defaultValue="0"
              required
            />
            <Field
              label="Minimum confirmation samples"
              name="samples"
              type="number"
              min="1"
              max="10000"
              defaultValue="30"
              required
            />
            <Field
              label="Minimum independent groups"
              name="groups"
              type="number"
              min="1"
              max="10000"
              defaultValue="3"
              required
            />
            <Field
              label="Training examples"
              name="examples"
              type="number"
              min="1"
              max="8"
              defaultValue="4"
              required
            />
            <Field
              label="Generation budget USD"
              name="budget"
              defaultValue="0.10"
              required
            />
          </Form>
        </details>
      )}
      {selected && (
        <section aria-label="Selected criteria version" key={selected.id}>
          <h3>Criteria {selected.id}</h3>
          <p>
            {selected.state} · {selected.review_status}. Plan hash:{" "}
            <code>{selected.plan_hash}</code>.
          </p>
          {admin &&
            selected.state === "PENDING" &&
            selected.generation_approval_status === "pending" && (
              <Form
                title="Approve generation budget"
                submit="Approve exact generation plan"
                onSubmit={async (data) => {
                  await json(api, path + "/approve-generation", {
                    plan_hash: selected.plan_hash,
                    approval_ref: data.approval,
                  });
                  await refresh();
                  return "Exact one-call budget approved.";
                }}
              >
                <Field
                  label="Generation approval reference"
                  name="approval"
                  required
                  maxLength={128}
                />
                <Check
                  label="I authorize this one provider call under the current capture policy and budget."
                  name="consent"
                  required
                />
              </Form>
            )}
          {analyst &&
            selected.state === "PENDING" &&
            selected.generation_approval_status === "approved" && (
              <Form
                title="Queue approved generation"
                submit="Queue generation once"
                onSubmit={async () => {
                  await json(api, path + "/runs", {
                    plan_hash: selected.plan_hash,
                  });
                  await refresh();
                  return "Generation queued. Refresh status; an uncertain call is never automatically replayed.";
                }}
              >
                <Check
                  label="Run this exact approved generation plan."
                  name="consent"
                  required
                />
              </Form>
            )}
          {admin && (
            <Form
              title="Read private criteria content"
              submit="Read authorized content"
              onSubmit={async (data) => {
                const request = ++generation.current;
                const value = await json(api, path + "/content", {
                  purpose: data.purpose,
                });
                if (request === generation.current) setContent(value);
                return "Content read recorded in the audit trail. It stays in this page's memory.";
              }}
            >
              <Field
                label="Criteria content read purpose"
                name="purpose"
                required
                maxLength={128}
              />
            </Form>
          )}
          {content && (
            <>
              <button onClick={() => setContent(null)}>
                Clear private criteria content
              </button>
              <details open>
                <summary>
                  Frozen owner requirements and training examples
                </summary>
                <pre className="review-text" tabIndex={0}>
                  {JSON.stringify(
                    {
                      requirements: content.requirements,
                      training: content.training,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              <h4>Proposed checks</h4>
              <pre className="review-text" tabIndex={0}>
                {JSON.stringify(
                  content.proposal ??
                    content.invalid_native_output ??
                    "Generation has not produced valid criteria.",
                  null,
                  2,
                )}
              </pre>
              {content.criteria && (
                <details>
                  <summary>Exact deterministic evaluator configuration</summary>
                  <pre className="review-text" tabIndex={0}>
                    {JSON.stringify(content.criteria, null, 2)}
                  </pre>
                </details>
              )}
              {admin && content.state === "COMPLETED" && (
                <Form
                  title="Review frozen criteria"
                  submit="Record criteria decision"
                  onSubmit={async (data) => {
                    await json(api, path + "/review", {
                      version_sha256: content.version_sha256,
                      expected_review_approval_id: content.review_approval_id,
                      decision: data.decision,
                      approval_ref: data.approval,
                    });
                    setContent(null);
                    await refresh();
                    return "Review recorded. Read the current content again before preparing an evaluation.";
                  }}
                >
                  <CriteriaSelect
                    label="Criteria decision"
                    name="decision"
                    options={[
                      ["reject", "Reject these checks"],
                      ["approve", "Approve these exact checks"],
                    ]}
                  />
                  <Field
                    label="Criteria review reference"
                    name="approval"
                    required
                    maxLength={128}
                  />
                  <Check
                    label="I reviewed the examples, metric meaning and fixed quality requirements. This is not independent judge calibration."
                    name="reviewed"
                    required
                  />
                </Form>
              )}
            </>
          )}
          {analyst && binding && (
            <Form
              title="Validate models with approved criteria"
              submit="Freeze validation plan"
              onSubmit={async (data) => {
                const body = {
                  dataset_id: data.dataset,
                  criteria_binding: binding,
                  baseline: {
                    key: "baseline",
                    provider: "openai",
                    model: data.baseline,
                    max_input_tokens: 24000,
                    max_output_tokens: 1024,
                  },
                  candidates: [
                    {
                      key: "candidate",
                      provider: "openai",
                      model: data.candidate,
                      max_input_tokens: 24000,
                      max_output_tokens: 1024,
                    },
                  ],
                  budget: {
                    max_usd: data.budget,
                    max_requests: Number(data.requests),
                  },
                  ...(semanticSelected
                    ? { episode_execution: { max_turns: Number(data.turns) } }
                    : {}),
                };
                const identity = await preparationIdentity(
                  body,
                  validationIdentity,
                );
                const row = await json(api, path + "/evaluation-draft", {
                  ...body,
                  request_id: identity.request_id,
                  content_nonce: identity.content_nonce,
                });
                reload();
                return `Validation plan ${row.id} frozen. Approve and queue it below in Experiment plans.`;
              }}
            >
              <CriteriaSelect
                label="Validation dataset"
                name="dataset"
                required
                options={datasets.map((d) => [d.id, `${d.workflow} · ${d.id}`])}
              />
              <Field
                label="Baseline model"
                name="baseline"
                defaultValue="gpt-4.1"
                required
              />
              <Field
                label="Candidate model"
                name="candidate"
                defaultValue="gpt-4.1-mini"
                required
              />
              <Field
                label="Validation budget USD"
                name="budget"
                defaultValue={semanticSelected ? "5" : "1"}
                required
              />
              {semanticSelected && (
                <>
                  <p className="hint">
                    Every episode has a separate reserved judge call in addition
                    to its bounded native turns. The exact plan forecasts all
                    target and judge costs before approval. Invalid or missing
                    judgments remain unknown.
                  </p>
                  <Field
                    label="Maximum native turns per episode"
                    name="turns"
                    type="number"
                    min="1"
                    max="32"
                    defaultValue="3"
                    required
                  />
                </>
              )}
              <Field
                label="Validation maximum calls"
                name="requests"
                type="number"
                min="1"
                max="100000"
                defaultValue="200"
                required
              />
            </Form>
          )}
          {analyst && selected.review_status === "APPROVED" && (
            <Form
              title="Confirm one validated candidate"
              submit="Freeze untouched-test confirmation"
              onSubmit={async (data) => {
                const body = {
                  validation_run_id: data.run,
                  candidate_key: data.candidate,
                  budget: {
                    max_usd: data.budget,
                    max_requests: Number(data.requests),
                  },
                };
                const identity = await preparationIdentity(
                  body,
                  confirmationIdentity,
                );
                const row = await json(api, path + "/confirmation", {
                  ...body,
                  request_id: identity.request_id,
                });
                reload();
                return `Confirmation plan ${row.id} frozen. Its budget needs separate approval. Test groups may be consumed only once.`;
              }}
            >
              <CriteriaSelect
                label="Completed validation run"
                name="run"
                required
                options={runs
                  .filter((r) => r.status === "COMPLETED")
                  .map((r) => [r.id, r.id])}
              />
              <Field
                label="Selected candidate key"
                name="candidate"
                defaultValue="candidate"
                required
              />
              <Field
                label="Confirmation budget USD"
                name="budget"
                defaultValue="1"
                required
              />
              <Field
                label="Confirmation maximum calls"
                name="requests"
                type="number"
                min="1"
                max="100000"
                defaultValue="200"
                required
              />
            </Form>
          )}
          {analyst &&
            ["PENDING", "QUEUED", "RUNNING"].includes(selected.state) && (
              <Form
                title="Cancel criteria generation"
                submit="Cancel without replay"
                onSubmit={async (data) => {
                  await json(api, path + "/cancel", { reason: data.reason });
                  await refresh();
                  return "Cancellation recorded. Any request already sent may still be billed.";
                }}
              >
                <Field
                  label="Cancellation reason"
                  name="reason"
                  required
                  maxLength={128}
                />
              </Form>
            )}
        </section>
      )}
    </section>
  );
}

function CriteriaSelect({
  label,
  name,
  options,
  required = false,
}: {
  label: string;
  name: string;
  options: string[][];
  required?: boolean;
}) {
  return (
    <Select label={label} name={name} required={required}>
      {options.map(([value, text]) => (
        <option key={value} value={value}>
          {text}
        </option>
      ))}
    </Select>
  );
}
