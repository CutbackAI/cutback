import React, { useEffect, useRef, useState } from "react";
import {
  type API,
  type Data,
  Field,
  Form,
  Select,
  explain,
  json,
  money,
} from "./ui";

export function GeneratedScenarios({
  api,
  base,
  datasets,
  admin,
  analyst,
  reload,
}: {
  api: API;
  base: string;
  datasets: Data[];
  admin: boolean;
  analyst: boolean;
  reload: () => void;
}) {
  const [rows, setRows] = useState<Data[]>([]);
  const [selected, setSelected] = useState<Data | null>(null);
  const [content, setContent] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  const [goals, setGoals] = useState(1);
  const generation = useRef(0);
  const draftIdentity = useRef<{ body: string; id: string } | null>(null);
  const importIdentity = useRef<{ body: string; id: string } | null>(null);
  function identity(body: Data, ref: typeof draftIdentity) {
    const encoded = JSON.stringify(body);
    if (!crypto.randomUUID)
      throw new Error("Use HTTPS or the loopback collector.");
    if (ref.current?.body !== encoded)
      ref.current = { body: encoded, id: crypto.randomUUID() };
    return ref.current.id;
  }
  useEffect(() => {
    const request = ++generation.current;
    api(`${base}/scenarios?limit=25&offset=${offset}`)
      .then((value) => {
        if (request === generation.current) setRows(value);
      })
      .catch((e) => {
        if (request === generation.current) setError(explain(e));
      });
    return () => {
      generation.current++;
    };
  }, [api, base, offset]);
  async function refresh(selection = selected) {
    const request = ++generation.current;
    const page = await api(`${base}/scenarios?limit=25&offset=${offset}`);
    if (request !== generation.current) return;
    setRows(page);
    if (selection) {
      const row = await api(`${base}/scenarios/${selection.id}`);
      if (request !== generation.current) return;
      setSelected(row);
      setContent(null);
    }
    reload();
  }
  const path = selected ? `${base}/scenarios/${selected.id}` : "";
  return (
    <section aria-label="Generated development scenarios">
      <h2>Generated development scenarios</h2>
      <p>
        Expand authorized training tasks with synthetic personas and declared
        coverage goals. Original questions, tool permissions, gold and task
        assertions stay fixed. Review the full proposal before importing
        development validation data.
      </p>
      <p className="hint">
        Generation needs one separately approved call. These static variations
        are development examples. Coverage is proposer-declared, population
        coverage is unknown, and generated cases are never independent test
        evidence.
      </p>
      {error && <p role="alert">{error}</p>}
      <button
        type="button"
        onClick={() => refresh().catch((e) => setError(explain(e)))}
      >
        Refresh scenarios
      </button>
      {!rows.length && <p>No scenario versions on this page.</p>}
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
              importIdentity.current = null;
            }}
          >
            Inspect scenario {row.id}
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
          Previous scenarios
        </button>
        <button
          disabled={rows.length < 25}
          onClick={() => {
            setOffset(offset + 25);
            setSelected(null);
            setContent(null);
          }}
        >
          Next scenarios
        </button>
      </div>
      {analyst && (
        <details className="editor">
          <summary>Expand training scenarios</summary>
          <Form
            title="Prepare scenario generation"
            submit="Freeze scenario generation plan"
            description="Stores an encrypted training snapshot and forecasts one call. No inference occurs during preparation."
            onSubmit={async (data) => {
              const body = {
                dataset_id: data.dataset,
                requirements: {
                  task_brief: data.brief,
                  coverage_goals: Array.from({ length: goals }, (_, i) => ({
                    key: `goal-${i + 1}`,
                    description: data[`goal_${i}`],
                  })),
                  max_scenarios: Number(data.variants),
                  max_personas: Number(data.personas),
                  max_additional_user_turns: Number(data.turns),
                },
                generator: {
                  key: "scenario-generator",
                  provider: "openai",
                  model: data.model,
                  max_input_tokens: 56000,
                  max_output_tokens: 4096,
                },
                budget: { max_usd: data.budget, max_requests: 1 },
                max_training_examples: Number(data.examples),
                transport: data.transport,
              };
              const row = await json(api, `${base}/scenarios/drafts`, {
                ...body,
                request_id: identity(body, draftIdentity),
              });
              setSelected(row);
              setContent(null);
              await refresh(row);
              return "Scenario plan frozen. An admin must approve this exact plan before it can run.";
            }}
          >
            <Select label="Scenario source dataset" name="dataset" required>
              {datasets.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.workflow_id || row.workflow || row.id}
                </option>
              ))}
            </Select>
            <Field
              label="Scenario task brief"
              name="brief"
              required
              maxLength={4000}
            />
            <Select
              label="Coverage goal count"
              name="goal_count"
              value={goals}
              onChange={(e) => setGoals(Number(e.target.value))}
            >
              {Array.from({ length: 8 }, (_, i) => (
                <option key={i} value={i + 1}>
                  {i + 1}
                </option>
              ))}
            </Select>
            {Array.from({ length: goals }, (_, i) => (
              <Field
                key={i}
                label={`Coverage goal ${i + 1}`}
                name={`goal_${i}`}
                required
                maxLength={500}
              />
            ))}
            <Select label="Scenario generator model" name="model">
              <option>gpt-4.1-mini</option>
              <option>gpt-4.1</option>
            </Select>
            <Select label="Scenario execution mode" name="transport">
              <option value="native_provider">
                Configured provider, separately approved cost
              </option>
              <option value="scripted_test_only">
                Owned synthetic demonstration only
              </option>
            </Select>
            <Field
              label="Scenario training examples"
              name="examples"
              type="number"
              min="1"
              max="8"
              defaultValue="4"
              required
            />
            <Field
              label="Maximum scenario variants"
              name="variants"
              type="number"
              min="1"
              max="8"
              defaultValue="4"
              required
            />
            <Field
              label="Maximum synthetic personas"
              name="personas"
              type="number"
              min="1"
              max="4"
              defaultValue="3"
              required
            />
            <Field
              label="Maximum additional user turns"
              name="turns"
              type="number"
              min="0"
              max="3"
              defaultValue="2"
              required
              hint="Only existing synthetic conversations can gain follow-ups within their original turn limit."
            />
            <Field
              label="Scenario generation budget USD"
              name="budget"
              defaultValue="0.10"
              required
            />
          </Form>
        </details>
      )}
      {selected && (
        <article aria-label="Selected scenario version">
          <h3>Scenario {selected.id}</h3>
          <p>
            {selected.state} · review {selected.review_status} · approval{" "}
            {selected.generation_approval_status}
          </p>
          {admin &&
            selected.state === "PENDING" &&
            selected.generation_approval_status === "pending" && (
              <Form
                title="Approve scenario generation budget"
                submit="Approve exact scenario budget"
                onSubmit={async (data) => {
                  await json(api, path + "/approve-generation", {
                    plan_hash: selected.plan_hash,
                    approval_ref: data.approval,
                  });
                  await refresh();
                  return "Exact scenario generation budget approved.";
                }}
              >
                <Field
                  label="Scenario budget approval reference"
                  name="approval"
                  required
                />
              </Form>
            )}
          {analyst &&
            selected.state === "PENDING" &&
            selected.generation_approval_status === "approved" && (
              <Form
                title="Queue scenario generation"
                submit="Queue approved scenario generation"
                onSubmit={async () => {
                  await json(api, path + "/runs", {
                    plan_hash: selected.plan_hash,
                  });
                  await refresh();
                  return "Scenario generation queued. Refresh to reconcile; an uncertain billable call is never replayed.";
                }}
              >
                <p>The worker executes this exact approved plan once.</p>
              </Form>
            )}
          {analyst &&
            ["PENDING", "QUEUED", "RUNNING"].includes(selected.state) && (
              <Form
                title="Cancel scenario generation"
                submit="Cancel this scenario generation"
                onSubmit={async (data) => {
                  await json(api, path + "/cancel", { reason: data.reason });
                  await refresh();
                  return "Cancellation recorded. Already dispatched costs remain recorded.";
                }}
              >
                <Field
                  label="Scenario cancellation reason"
                  name="reason"
                  required
                />
              </Form>
            )}
          {admin && (
            <Form
              title="Read private scenario content"
              submit="Load private scenario content"
              onSubmit={async (data) => {
                const request = ++generation.current;
                const value = await json(api, path + "/content", {
                  purpose: data.purpose,
                });
                if (request !== generation.current)
                  return "Selection changed; content was discarded.";
                setContent(value);
                return "Private scenario content loaded in memory.";
              }}
            >
              <Field
                label="Scenario content access purpose"
                name="purpose"
                required
              />
            </Form>
          )}
          {content && (
            <>
              <button
                onClick={() => {
                  generation.current++;
                  setContent(null);
                }}
              >
                Clear private scenario content
              </button>
              <pre tabIndex={0}>
                {JSON.stringify(
                  {
                    requirements: content.requirements,
                    proposal: content.proposal,
                    preview: content.preview,
                    invalid_native_output: content.invalid_native_output,
                  },
                  null,
                  2,
                )}
              </pre>
            </>
          )}
          {admin && content?.state === "COMPLETED" && (
            <Form
              title="Review generated scenarios"
              submit="Record scenario review"
              onSubmit={async (data) => {
                const value = await json(api, path + "/review", {
                  version_sha256: content.version_sha256,
                  expected_review_approval_id: content.review_approval_id,
                  decision: data.decision,
                  approval_ref: data.approval,
                });
                await refresh(value);
                return "Owner review recorded. This does not establish independent labels or measured population coverage.";
              }}
            >
              <Select label="Scenario review decision" name="decision">
                <option value="approve">Approve development scenarios</option>
                <option value="reject">Reject development scenarios</option>
              </Select>
              <Field
                label="Scenario review reference"
                name="approval"
                required
              />
            </Form>
          )}
          {admin &&
            selected.state === "COMPLETED" &&
            selected.review_status === "APPROVED" && (
              <Form
                title="Import reviewed development scenarios"
                submit="Import reviewed development dataset"
                onSubmit={async (data) => {
                  const body = {
                    version_sha256: selected.version_sha256,
                    review_approval_id: selected.review_approval_id,
                    approval_ref: data.approval,
                  };
                  const value = await json(api, path + "/imports", {
                    ...body,
                    request_id: identity(body, importIdentity),
                  });
                  reload();
                  return `Development dataset ${value.dataset_id} is ready for separately approved validation. It contains no test split.`;
                }}
              >
                <Field
                  label="Scenario dataset import reference"
                  name="approval"
                  required
                />
              </Form>
            )}
        </article>
      )}
    </section>
  );
}
