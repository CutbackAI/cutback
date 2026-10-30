import React, { useEffect, useRef, useState } from "react";
import {
  type API,
  type Data,
  Field,
  Form,
  Select,
  json,
  explain,
  money,
} from "./ui";

type Identity = {
  fingerprint: string;
  request_id: string;
  content_nonce: string;
};
async function identify(body: Data, ref: { current: Identity | null }) {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(body)),
  );
  const fingerprint = Array.from(new Uint8Array(bytes), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  if (ref.current?.fingerprint !== fingerprint)
    ref.current = {
      fingerprint,
      request_id: crypto.randomUUID(),
      content_nonce: crypto.randomUUID(),
    };
  return ref.current;
}
function ceiling(data: Data) {
  return {
    max_usd: String(data.max_usd),
    max_requests: Number(data.max_requests),
  };
}
function BudgetFields() {
  return (
    <>
      <Field
        label="Maximum USD for this stage"
        name="max_usd"
        type="number"
        min="0.000001"
        step="0.000001"
        required
      />
      <Field
        label="Maximum requests for this stage"
        name="max_requests"
        type="number"
        min="1"
        max="10000"
        required
      />
    </>
  );
}

export function LearnedRouting({
  api: request,
  base,
  datasets,
  runs,
  admin,
  reload,
}: {
  api: API;
  base: string;
  datasets: Data[];
  runs: Data[];
  admin: boolean;
  reload: () => void;
}) {
  const [fits, setFits] = useState<Data[]>([]),
    [training, setTraining] = useState<Data[]>([]);
  const [moreFits, setMoreFits] = useState(false),
    [moreTraining, setMoreTraining] = useState(false);
  const [fit, setFit] = useState<Data | null>(null),
    [plan, setPlan] = useState<Data | null>(null);
  const [privateContent, setPrivateContent] = useState<Data | null>(null);
  const [reviewedHash, setReviewedHash] = useState("");
  const [execution, setExecution] = useState<Data | null>(null),
    [error, setError] = useState("");
  const [report, setReport] = useState<Data | null>(null);
  const [provider, setProvider] = useState("openai");
  const trainingIdentity = useRef<Identity | null>(null),
    fitIdentity = useRef<Identity | null>(null);
  const validationIdentity = useRef<Identity | null>(null),
    confirmationIdentity = useRef<Identity | null>(null);
  const generation = useRef(0);
  const scope = useRef({ base, admin });
  scope.current = { base, admin };
  const api: API = async (path, options) => {
    const value = await request(path, options);
    if (scope.current.base !== base || scope.current.admin !== admin)
      throw new Error("PROJECT_CHANGED_REOPEN_ROUTING");
    return value;
  };
  async function refresh() {
    const current = ++generation.current;
    const [nextFits, nextTraining] = await Promise.all([
      api(base + "/routing/fits"),
      api(base + "/routing/training-runs"),
    ]);
    if (current === generation.current) {
      setFits(nextFits);
      setTraining(nextTraining);
      setMoreFits(nextFits.length === 50);
      setMoreTraining(nextTraining.length === 50);
    }
  }
  async function loadMore(kind: "fits" | "training-runs") {
    const rows = kind === "fits" ? fits : training;
    const next = await api(base + `/routing/${kind}?offset=${rows.length}`);
    const identities = new Set(rows.map((row) => row.id));
    const combined = [
      ...rows,
      ...next.filter((row: Data) => !identities.has(row.id)),
    ];
    if (kind === "fits") {
      setFits(combined);
      setMoreFits(next.length === 50);
    } else {
      setTraining(combined);
      setMoreTraining(next.length === 50);
    }
  }
  useEffect(() => {
    setFit(null);
    setPlan(null);
    setPrivateContent(null);
    setReviewedHash("");
    setExecution(null);
    setReport(null);
    trainingIdentity.current =
      fitIdentity.current =
      validationIdentity.current =
      confirmationIdentity.current =
        null;
    refresh().catch((value) => setError(explain(value)));
    return () => {
      generation.current += 1;
    };
  }, [base, admin]);
  function selectedPlan(next: Data) {
    setPlan(next);
    setPrivateContent(null);
    setReviewedHash("");
    setExecution(null);
    setReport(null);
    reload();
  }
  function clearPlan() {
    setPlan(null);
    setPrivateContent(null);
    setReviewedHash("");
    setExecution(null);
    setReport(null);
  }
  function selectedFit(next: Data | null) {
    if (next?.id !== fit?.id) {
      validationIdentity.current = confirmationIdentity.current = null;
      setPlan(null);
      setExecution(null);
      setReport(null);
    }
    setFit(next);
    setPrivateContent(null);
    setReviewedHash("");
  }
  const models =
    provider === "openai"
      ? ["gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"]
      : ["claude-sonnet-4-6", "claude-haiku-4-5"];
  const validations = runs.filter(
    (row) =>
      row.specification?.routing_execution?.fit_id === fit?.id &&
      row.specification?.split === "validation" &&
      row.status === "COMPLETED",
  );
  async function executionFor(planId: string) {
    const value = await api(base + `/experiments/plans/${planId}/execution`);
    setExecution(value);
    setReport(
      value.run_id ? await api(base + `/experiments/${value.run_id}`) : null,
    );
    return value;
  }
  async function inspectRun(row: Data) {
    setPlan(null);
    setPrivateContent(null);
    setReviewedHash("");
    setReport(await api(base + `/experiments/${row.id}`));
    setExecution(null);
  }
  const routingRuns = runs.filter(
    (row) => row.specification?.routing_execution,
  );

  return (
    <section aria-label="Learned input-size routing">
      <h2>Learn input-size routing</h2>
      <p>
        Compare native models on training cases, fit a bounded policy, then
        validate it and confirm once on untouched groups. Training fit is not
        independent quality evidence. Fitting makes no provider request; each
        comparison has its own reviewed budget.
      </p>
      <p className="hint">
        Input size is a declared feature, not a claim of task difficulty. The
        policy falls back to the baseline for longer inputs within its approved
        input bound. Router hosting and fitting costs stay unknown unless
        separately measured. Application integration requires its own review.
      </p>
      {error && <p role="alert">{error}</p>}
      {admin && (
        <details>
          <summary>1. Prepare a training comparison</summary>
          <Form
            title="Prepare routing training"
            submit="Freeze training plan"
            description="Use permitted labeled text cases. Preparing makes no provider request."
            onSubmit={async (data) => {
              clearPlan();
              const body = {
                dataset_id: String(data.dataset_id),
                transport: String(data.transport),
                baseline: {
                  key: "routing-baseline",
                  provider,
                  model: String(data.baseline),
                  instruction: String(data.instruction),
                  max_input_tokens: Number(data.max_input),
                  max_output_tokens: Number(data.max_output),
                },
                candidates: [
                  {
                    key: "routing-alternative",
                    provider,
                    model: String(data.candidate),
                    instruction: String(data.instruction),
                    max_input_tokens: Number(data.max_input),
                    max_output_tokens: Number(data.max_output),
                  },
                ],
                metric: {
                  kind: "exact_label",
                  minimum: Number(data.minimum),
                  max_regression: Number(data.max_regression),
                  min_test_samples: Number(data.min_samples),
                  min_test_groups: Number(data.min_groups),
                },
                budget: ceiling(data),
                max_cases: Number(data.max_cases),
              };
              const identity = await identify(body, trainingIdentity);
              const next = await json(api, base + "/routing/training-drafts", {
                ...body,
                request_id: identity.request_id,
                content_nonce: identity.content_nonce,
              });
              selectedPlan({ ...next, budget: body.budget });
              await refresh();
              return "Training plan prepared. Review its exact content before approving its budget.";
            }}
          >
            <Select label="Routing dataset" name="dataset_id" required>
              {datasets.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.workflow} · {row.id}
                </option>
              ))}
            </Select>
            <Select
              label="Routing provider"
              name="provider"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
            >
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </Select>
            <Select
              label="Routing response source"
              name="transport"
              defaultValue="scripted_test_only"
            >
              <option value="scripted_test_only">Fixture responses</option>
              <option value="native_provider">
                Provider responses using approved vendor budget
              </option>
            </Select>
            <p className="hint">
              Fixture responses require the matching local sample worker. A live
              provider worker cannot execute a fixture-only plan.
            </p>
            <Select
              key={provider + "baseline"}
              label="Routing baseline model"
              name="baseline"
            >
              {models.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </Select>
            <Select
              key={provider + "candidate"}
              label="Routing alternative model"
              name="candidate"
              defaultValue={models[1]}
            >
              {models.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </Select>
            <label className="field">
              Shared task instruction
              <textarea name="instruction" required maxLength={16000} />
            </label>
            <Field
              label="Maximum input tokens per target"
              name="max_input"
              type="number"
              min="4097"
              max="100000"
              defaultValue="8192"
              required
            />
            <Field
              label="Maximum output tokens per target"
              name="max_output"
              type="number"
              min="16"
              max="16384"
              defaultValue="256"
              required
            />
            <Field
              label="Required exact-label score"
              name="minimum"
              type="number"
              min="0"
              max="1"
              step="0.01"
              defaultValue="1"
              required
            />
            <Field
              label="Maximum score regression"
              name="max_regression"
              type="number"
              min="0"
              max="1"
              step="0.01"
              defaultValue="0"
              required
            />
            <Field
              label="Required confirmation samples"
              name="min_samples"
              type="number"
              min="1"
              max="10000"
              defaultValue="30"
              required
            />
            <Field
              label="Required confirmation groups"
              name="min_groups"
              type="number"
              min="1"
              max="10000"
              defaultValue="3"
              required
            />
            <Field
              label="Maximum training cases"
              name="max_cases"
              type="number"
              min="2"
              max="256"
              defaultValue="256"
              required
            />
            <BudgetFields />
          </Form>
        </details>
      )}

      <h3>Training plans and executions</h3>
      {!training.length ? (
        <p>No routing training plans yet.</p>
      ) : (
        <ul>
          {training.map((row) => (
            <li key={row.id}>
              <button
                onClick={() => {
                  selectedPlan(row);
                  executionFor(row.id).catch((value) =>
                    setError(explain(value)),
                  );
                }}
              >
                Inspect training plan {row.id}
              </button>{" "}
              · {row.status || row.approval_status}{" "}
              {row.run_id && <code>{row.run_id}</code>}
            </li>
          ))}
        </ul>
      )}
      <h3>Routing comparison results</h3>
      <p className="hint">
        Routing results from the latest 50 project comparisons loaded here. Use
        the experiment history for other retained runs. Training plans have
        their own list above.
      </p>
      {moreTraining && (
        <button
          onClick={() =>
            loadMore("training-runs").catch((value) => setError(explain(value)))
          }
        >
          Load more routing training plans
        </button>
      )}
      {!routingRuns.length ? (
        <p>No routing comparisons yet.</p>
      ) : (
        <ul>
          {routingRuns.map((row) => (
            <li key={row.id}>
              <button
                onClick={() =>
                  inspectRun(row).catch((value) => setError(explain(value)))
                }
              >
                Read{" "}
                {row.specification.split === "train"
                  ? "training"
                  : row.specification.split}{" "}
                result {row.id}
              </button>{" "}
              · {row.status}
            </li>
          ))}
        </ul>
      )}
      {admin && (
        <Form
          title="Fit routing from completed training"
          submit="Fit routing policy"
          description="Requires complete canonical call receipts, labeled training cases and separate groups. This does not execute inference."
          onSubmit={async (data) => {
            const body = {
              training_run_id: String(data.training_run_id),
              purpose: String(data.purpose),
              options: {
                max_routes: Number(data.max_routes),
                min_leaf_cases: Number(data.min_leaf_cases),
                min_training_groups: Number(data.min_training_groups),
              },
            };
            const identity = await identify(body, fitIdentity);
            const next = await json(api, base + "/routing/fits", {
              ...body,
              request_id: identity.request_id,
            });
            selectedFit(next);
            await refresh();
            return "Policy fitted on training. Review it before preparing independent validation.";
          }}
        >
          <Select
            label="Completed routing training run"
            name="training_run_id"
            required
          >
            {training
              .filter((row) => row.status === "COMPLETED")
              .map((row) => (
                <option key={row.run_id} value={row.run_id}>
                  {row.run_id}
                </option>
              ))}
          </Select>
          <Field
            label="Maximum routing leaves including fallback"
            name="max_routes"
            type="number"
            min="2"
            max="8"
            defaultValue="4"
            required
          />
          <Field
            label="Minimum cases in each fitted leaf"
            name="min_leaf_cases"
            type="number"
            min="1"
            max="256"
            defaultValue="5"
            required
          />
          <Field
            label="Minimum training groups for fitting"
            name="min_training_groups"
            type="number"
            min="2"
            max="256"
            defaultValue="3"
            required
          />
          <Field
            label="Routing fit purpose reference"
            name="purpose"
            required
          />
        </Form>
      )}
      <label className="field">
        Retained routing policy
        <select
          value={fit?.id || ""}
          onChange={(event) =>
            selectedFit(
              fits.find((row) => row.id === event.target.value) || null,
            )
          }
        >
          <option value="">Select a fitted policy</option>
          {fits.map((row) => (
            <option key={row.id} value={row.id}>
              {row.id} · {row.training_cases} training cases
            </option>
          ))}
        </select>
      </label>
      {moreFits && (
        <button
          onClick={() =>
            loadMore("fits").catch((value) => setError(explain(value)))
          }
        >
          Load more fitted routing policies
        </button>
      )}
      {admin && fit && (
        <>
          <Form
            title="Read private fitted routing policy"
            submit="Read fitted policy"
            onSubmit={async (data) => {
              setPrivateContent(
                await json(api, base + `/routing/fits/${fit.id}/content`, {
                  purpose: String(data.purpose),
                }),
              );
              return "Private policy loaded into this tab only.";
            }}
          >
            <Field label="Fitted policy read purpose" name="purpose" required />
          </Form>
          <Form
            title="Prepare independent routing validation"
            submit="Freeze routing validation"
            onSubmit={async (data) => {
              clearPlan();
              const body = {
                budget: ceiling(data),
                purpose: String(data.purpose),
              };
              const identity = await identify(body, validationIdentity);
              selectedPlan(
                await json(
                  api,
                  base + `/routing/fits/${fit.id}/validation-drafts`,
                  {
                    ...body,
                    request_id: identity.request_id,
                    content_nonce: identity.content_nonce,
                  },
                ),
              );
              return "Validation prepared with the frozen policy and original quality requirements. Its budget is not approved.";
            }}
          >
            <BudgetFields />
            <Field label="Routing validation purpose" name="purpose" required />
          </Form>
          <Form
            title="Prepare untouched routing confirmation"
            submit="Freeze routing confirmation"
            onSubmit={async (data) => {
              clearPlan();
              const body = {
                validation_run_id: String(data.validation_run_id),
                budget: ceiling(data),
                purpose: String(data.purpose),
              };
              const identity = await identify(body, confirmationIdentity);
              selectedPlan(
                await json(
                  api,
                  base + `/routing/fits/${fit.id}/confirmation-drafts`,
                  { ...body, request_id: identity.request_id },
                ),
              );
              return "Untouched confirmation prepared. Review and authorize this separate budget before queueing.";
            }}
          >
            <Select
              label="Completed routing validation run"
              name="validation_run_id"
              required
            >
              {validations.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.id}
                </option>
              ))}
            </Select>
            <BudgetFields />
            <Field
              label="Routing confirmation purpose"
              name="purpose"
              required
            />
          </Form>
        </>
      )}

      {plan && (
        <>
          <h3>Selected routing plan</h3>
          <p>
            <code>{plan.id}</code> · hash <code>{plan.plan_hash}</code>
          </p>
          <p>
            Maximum budget{" "}
            {money(
              plan.budget?.max_usd ??
                plan.specification?.budget?.max_usd ??
                plan.summary?.budget?.max_usd,
            )}
            . Inspect the complete frozen specification below for request and
            dollar ceilings.
          </p>
          {plan.fits_budget === false && (
            <p role="alert">
              This frozen comparison does not fit its dollar or request ceiling.
              Prepare a new bounded plan; approval cannot increase its ceiling.
            </p>
          )}
          {plan.worst_case_modeled_usd !== undefined && (
            <p>
              Modeled maximum: {money(plan.worst_case_modeled_usd)} across{" "}
              {plan.requests} requests. Fits this stage's budget:{" "}
              {plan.fits_budget ? "Yes" : "No"}.
            </p>
          )}
          {admin && (
            <Form
              title="Review exact routing plan"
              submit="Read exact routing plan"
              onSubmit={async (data) => {
                const value = await json(
                  api,
                  base + `/experiments/plans/${plan.id}/content`,
                  { purpose: String(data.purpose) },
                );
                if (value.plan_hash !== plan.plan_hash)
                  throw new Error(
                    "Frozen plan changed. Refresh the selected plan.",
                  );
                setPrivateContent(value);
                setPlan({ ...plan, budget: value.specification.budget });
                setReviewedHash(value.plan_hash);
                return "Exact private plan read. Inspect all targets, bounds and ceilings before approval.";
              }}
            >
              <Field
                label="Routing plan read purpose"
                name="purpose"
                required
              />
            </Form>
          )}
          {admin &&
            reviewedHash === plan.plan_hash &&
            plan.fits_budget !== false &&
            (execution?.approval_status ??
              plan.approval_status ??
              "pending") === "pending" && (
              <Form
                title="Authorize exact routing stage"
                submit="Approve this routing budget"
                onSubmit={async (data) => {
                  await json(
                    api,
                    base + `/experiments/plans/${plan.id}/approve`,
                    {
                      plan_hash: plan.plan_hash,
                      approval_ref: String(data.approval),
                    },
                  );
                  await executionFor(plan.id);
                  await refresh();
                  return "Only this exact stage is approved.";
                }}
              >
                <Field
                  label="Routing stage approval reference"
                  name="approval"
                  required
                />
              </Form>
            )}
          {admin &&
            (execution?.approval_status === "approved" ||
              plan.approval_status === "approved") && (
              <Form
                title="Queue approved routing stage"
                submit="Queue this approved routing stage"
                onSubmit={async () => {
                  await json(api, base + "/experiments", { plan_id: plan.id });
                  await executionFor(plan.id);
                  await refresh();
                  reload();
                  return "Saved approved run queued. Reconcile execution if a reply was interrupted; uncertain paid work is never replayed.";
                }}
              >
                <p>
                  This can spend the stage's already authorized budget through
                  the configured worker.
                </p>
              </Form>
            )}
          <button
            onClick={() => {
              executionFor(plan.id)
                .then(() => {
                  refresh().catch((value) => setError(explain(value)));
                  reload();
                })
                .catch((value) => setError(explain(value)));
            }}
          >
            Refresh routing execution
          </button>
          {execution && (
            <p role="status">
              Approval: {execution.approval_status}. Execution:{" "}
              {execution.run_status || "Not queued"}.
            </p>
          )}
          {admin && report?.status === "BLOCKED" && report.requests === 0 && (
            <Form
              title="Recheck unstarted routing stage"
              submit="Recheck routing eligibility"
              onSubmit={async () => {
                await json(api, base + `/experiments/${report.id}/recheck`, {});
                await executionFor(plan.id);
                return "The same unstarted run was requeued. No new budget or paid retry was created.";
              }}
            >
              <p>
                Use after correcting the worker or policy. Started or uncertain
                billable work cannot use this action.
              </p>
            </Form>
          )}
        </>
      )}
      {report && <RoutingResult report={report} />}
      {admin && privateContent && (
        <div>
          <h3>Private routing review</h3>
          <button
            onClick={() => {
              setPrivateContent(null);
              setReviewedHash("");
            }}
          >
            Clear private routing review
          </button>
          {privateContent.content?.fit?.candidate?.routes && (
            <div
              className="table-scroll"
              tabIndex={0}
              role="region"
              aria-label="Fitted route bounds"
            >
              <table>
                <caption>
                  Fitted route bounds; the first matching bound selects one
                  target
                </caption>
                <thead>
                  <tr>
                    <th>Maximum UTF-8 input bytes</th>
                    <th>Provider</th>
                    <th>Target model</th>
                    <th>Output token cap</th>
                  </tr>
                </thead>
                <tbody>
                  {privateContent.content.fit.candidate.routes.map(
                    (row: Data) => (
                      <tr key={row.max_input_bytes}>
                        <td>{row.max_input_bytes}</td>
                        <td>{row.target.provider}</td>
                        <td>{row.target.model}</td>
                        <td>{row.target.max_output_tokens}</td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
          )}
          {privateContent.content?.fit && (
            <p>
              Fit-selected native cost on training observations:{" "}
              {money(
                privateContent.content.fit.fitted_training_native_cost_usd,
              )}
              . Baseline on the same observations:{" "}
              {money(
                privateContent.content.fit.baseline_training_native_cost_usd,
              )}
              . This policy still needs separate validation and untouched
              confirmation.
            </p>
          )}
          <details>
            <summary>Inspect the complete private specification</summary>
            <pre>{JSON.stringify(privateContent, null, 2)}</pre>
          </details>
        </div>
      )}
    </section>
  );
}

function RoutingResult({ report }: { report: Data }) {
  const result = report.results || {};
  const setup = result.routing_setup_cost;
  const transport = report.specification?.routing_execution?.transport;
  return (
    <section aria-label="Routing run evidence">
      <h3>Routing run evidence</h3>
      <p className="notice">
        {transport === "scripted_test_only"
          ? "Fixture responses and usage: these costs and checks are a synthetic demonstration, not real-model performance or customer savings."
          : transport === "native_provider"
            ? "Native provider responses. Recorded usage and modeled prices are not invoice reconciliation or independent customer-quality verification."
            : "Response provenance is not established for this retained result."}
      </p>
      <p>
        <code>{report.id}</code> · {report.status} · {report.requests} native
        requests.
      </p>
      <p>
        Recorded target spend: {money(report.spent_usd)}. Billing completeness:{" "}
        {result.billing_complete === true
          ? "Complete"
          : "Incomplete or unknown"}
        .
      </p>
      {report.specification?.split === "train" ? (
        <p>
          Training observations fit a policy. They cannot establish independent
          quality or authorize an application change.
        </p>
      ) : (
        <>
          <p>
            Untouched confirmation:{" "}
            {result.lineage?.fresh_test_claim === true
              ? "Fresh held-out groups recorded"
              : "Not established"}
            . Current training authority:{" "}
            {result.routing_source_current === true
              ? "Verified at completion"
              : "Unavailable or not established"}
            .
          </p>
          <div
            className="table-scroll"
            tabIndex={0}
            role="region"
            aria-label="Routing quality comparisons"
          >
            <table>
              <caption>
                Declared quality checks; these are not universal task success
              </caption>
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>Declared gate</th>
                  <th>Evidence status</th>
                </tr>
              </thead>
              <tbody>
                {(result.recommendations || []).map((row: Data) => (
                  <tr key={row.candidate.key}>
                    <th scope="row">{row.candidate.key}</th>
                    <td>
                      {row.quality_gate_passed === true
                        ? "Passed"
                        : "Not passed"}
                    </td>
                    <td>{row.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {setup && (
        <>
          <p>
            Shared training setup: {money(setup.training_known_cost_usd)}. Count
            once using setup key <code>{setup.shared_setup_key}</code> across
            validation and confirmation. It is separate from each run's target
            spend.
          </p>
          <p>
            Fit compute: {money(setup.fit_compute_usd)}. Router hosting:{" "}
            {money(setup.routing_hosting_cost_usd)}. Amortized full task cost:{" "}
            {money(setup.amortized_per_task_usd)}.
          </p>
        </>
      )}
      <p>
        Source change eligibility: not established by this workflow. Review
        application integration and affected checks separately.
      </p>
      <details>
        <summary>Inspect redacted routing evidence</summary>
        <pre>{JSON.stringify(report, null, 2)}</pre>
      </details>
    </section>
  );
}
