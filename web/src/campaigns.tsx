import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, type Data, explain, json, money } from "./ui";
import { NativeContracts } from "./native-contracts";
import { gatewayModeledUSD } from "./gateway-money";

type Brief = components["schemas"]["CampaignBrief"];
type Detail = components["schemas"]["CampaignDetail"];
type Template = components["schemas"]["CampaignTemplate"];
type Snapshot = components["schemas"]["TaskSnapshotBrief"];
type CandidateContent = components["schemas"]["CampaignCandidateContent"];
function nativeCampaignPlan(value: Detail["specification"]): Data {
  const specification = value as Data;
  return {
    ...specification.template,
    candidates: [
      ...(specification.static_portfolio ||
        specification.template?.candidates ||
        []),
      specification.generator?.model,
    ].filter(Boolean),
  };
}
const active = new Set([
  "READY_FOR_APPROVAL",
  "QUEUED",
  "RUNNING",
  "WAITING_PROPOSAL",
  "WAITING_VALIDATION",
  "WAITING_CONFIRMATION",
]);
const decisions: Record<string, string> = {
  synthetic_confirmation_passed_real_data_required:
    "The selected alternative passed synthetic confirmation. Representative independent data is still required before an application release can be recommended.",
  prepare_application_checks:
    "The selected alternative passed the recorded confirmation gates. Ask your coding agent to prepare the exact diff and rollback, then verify every affected application's checks before PR review.",
  reject_no_quality_preserving_lower_cost_candidate:
    "No evaluated alternative preserved the required quality at a lower known modeled cost. Keep the current application unchanged.",
  confirmation_requires_review_or_rejected:
    "Confirmation did not establish a usable improvement. Review the retained quality, cost and evidence limits before proposing an application change.",
};
const searchDecisions: Record<string, string> = {
  validate_static_alternative_before_instruction_generation:
    "Validate the prepared model or output-cap alternative before generating instructions.",
  static_alternative_passed_skip_instruction_generation:
    "The static alternative passed validation. Instruction generation was skipped.",
  static_alternative_failed_use_reserved_instruction_search:
    "The static alternative failed validation, so this campaign selected its reserved instruction-search stage.",
  static_alternative_failed_no_generation_budget:
    "The static alternative failed validation and no instruction-search round fits this budget. Keep the current application unchanged.",
  unchanged_control_skip_static_trial:
    "No distinct static alternative was available. This campaign skips the duplicate reference trial.",
};

export function Campaigns({
  api,
  project,
  canWrite,
  canApprove,
}: {
  api: API;
  project: string;
  canWrite: boolean;
  canApprove: boolean;
}) {
  const base = `/projects/${project}`;
  const [rows, setRows] = useState<Brief[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateCursor, setTemplateCursor] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [snapshotCursor, setSnapshotCursor] = useState<string | null>(null);
  const [environments, setEnvironments] = useState<
    { id: string; name: string }[]
  >([]);
  const [environment, setEnvironment] = useState("");
  const [template, setTemplate] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [approved, setApproved] = useState(false);
  const [reference, setReference] = useState("");
  const [purpose, setPurpose] = useState("");
  const [content, setContent] = useState<CandidateContent | null>(null);
  const [prepareOpen, setPrepareOpen] = useState(false);
  const selectedHeading = useRef<HTMLHeadingElement>(null);
  const generation = useRef(0);
  const snapshotGeneration = useRef(0);
  const selection = useRef(0);
  const request = useRef<{ key: string; id: string } | null>(null);

  useEffect(() => {
    if (detail) {
      selectedHeading.current?.focus({ preventScroll: true });
      selectedHeading.current?.scrollIntoView({ block: "start" });
    }
  }, [detail?.id]);

  useEffect(() => {
    const version = ++generation.current;
    selection.current++;
    setLoading(true);
    setError("");
    setDetail(null);
    setContent(null);
    setPurpose("");
    setApproved(false);
    request.current = null;
    Promise.all([
      api(`${base}/campaigns?limit=20`),
      api(`${base}/campaign-templates?limit=20`),
      api(`${base}/environments`),
    ])
      .then(([campaigns, plans, envs]) => {
        if (version !== generation.current) return;
        setRows(campaigns.items);
        setCursor(campaigns.next_after);
        setTemplates(plans.items);
        setTemplateCursor(plans.next_after);
        setTemplate(plans.items[0]?.id || "");
        setEnvironments(envs.items);
        setEnvironment(envs.items[0]?.id || "");
      })
      .catch((e) => {
        if (version === generation.current) setError(explain(e));
      })
      .finally(() => {
        if (version === generation.current) setLoading(false);
      });
    return () => {
      generation.current++;
      selection.current++;
    };
  }, [api, base]);

  useEffect(() => {
    const version = ++snapshotGeneration.current;
    setSnapshots([]);
    setSnapshot("");
    setSnapshotCursor(null);
    if (!environment) return;
    api(`${base}/task-cost-snapshots?environment_id=${environment}&limit=20`)
      .then((value) => {
        if (version === snapshotGeneration.current) {
          setSnapshots(value.items);
          setSnapshotCursor(value.next_after);
        }
      })
      .catch((e) => {
        if (version === snapshotGeneration.current) setError(explain(e));
      });
    return () => {
      snapshotGeneration.current++;
    };
  }, [api, base, environment]);

  async function inspect(id: string) {
    const version = ++selection.current;
    setApproved(false);
    setReference("");
    setContent(null);
    setPurpose("");
    setError("");
    try {
      const value = await api(`${base}/campaigns/${id}`);
      if (version === selection.current) {
        setDetail(value);
        setRows((old) => old.map((r) => (r.id === value.id ? value : r)));
      }
    } catch (e) {
      if (version === selection.current) setError(explain(e));
    }
  }
  async function readCandidate(id: string) {
    if (!detail || !purpose) return;
    const version = selection.current;
    const projectVersion = generation.current;
    setContent(null);
    setBusy(true);
    setError("");
    try {
      const value = await json(
        api,
        `${base}/campaigns/${detail.id}/candidates/${id}/content`,
        { purpose },
      );
      if (version === selection.current) setContent(value);
    } catch (e) {
      if (version === selection.current) setError(explain(e));
    } finally {
      if (projectVersion === generation.current) setBusy(false);
    }
  }
  async function more(kind: "campaigns" | "templates" | "snapshots") {
    const version = generation.current;
    const envVersion = snapshotGeneration.current;
    const after =
      kind === "campaigns"
        ? cursor
        : kind === "templates"
          ? templateCursor
          : snapshotCursor;
    const path =
      kind === "templates"
        ? "campaign-templates"
        : kind === "snapshots"
          ? "task-cost-snapshots"
          : "campaigns";
    setBusy(true);
    setError("");
    try {
      const page = await api(
        `${base}/${path}?limit=20&after=${after}${kind === "snapshots" ? `&environment_id=${environment}` : ""}`,
      );
      if (
        version !== generation.current ||
        (kind === "snapshots" && envVersion !== snapshotGeneration.current)
      )
        return;
      if (kind === "campaigns") {
        setRows((old) => [...old, ...page.items]);
        setCursor(page.next_after);
      }
      if (kind === "templates") {
        setTemplates((old) => [...old, ...page.items]);
        setTemplateCursor(page.next_after);
      }
      if (kind === "snapshots") {
        setSnapshots((old) => [...old, ...page.items]);
        setSnapshotCursor(page.next_after);
      }
    } catch (e) {
      if (version === generation.current) setError(explain(e));
    } finally {
      if (version === generation.current) setBusy(false);
    }
  }
  async function prepare(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = {
      version: "cutback.improvement-campaign.v3",
      template_plan_id: template,
      baseline_snapshot_id: snapshot,
      generator_model: {
        key: "proposer",
        provider: "openai",
        model: "gpt-4.1-mini",
        max_input_tokens: 24000,
        max_output_tokens: 2048,
      },
      budget: {
        max_usd: form.get("usd"),
        max_requests: Number(form.get("requests")),
        max_wall_seconds: Number(form.get("wall")),
      },
      rounds: Number(form.get("rounds")),
      variants_per_round: Number(form.get("variants")),
      conservative_savings_per_success_usd:
        String(form.get("savings") || "") || null,
      forecast_successful_tasks: form.get("volume")
        ? Number(form.get("volume"))
        : null,
    };
    const key = JSON.stringify(body);
    if (request.current?.key !== key)
      request.current = { key, id: crypto.randomUUID() };
    const version = generation.current;
    setBusy(true);
    setError("");
    try {
      const value: Brief = await json(api, `${base}/campaigns/draft`, {
        ...body,
        request_id: request.current.id,
      });
      if (version !== generation.current) return;
      setRows((old) => [value, ...old.filter((r) => r.id !== value.id)]);
      setPrepareOpen(false);
      await inspect(value.id);
    } catch (e) {
      if (version === generation.current) setError(explain(e));
    } finally {
      if (version === generation.current) setBusy(false);
    }
  }
  async function decide(action: "approve" | "cancel") {
    if (!detail) return;
    const chosen = detail;
    const version = generation.current;
    setBusy(true);
    setError("");
    try {
      const row: Brief = await json(
        api,
        `${base}/campaigns/${chosen.id}/${action}`,
        action === "approve"
          ? { campaign_hash: chosen.campaign_hash, approval_ref: reference }
          : {},
      );
      if (version !== generation.current) return;
      setRows((old) => old.map((r) => (r.id === row.id ? row : r)));
      await inspect(row.id);
    } catch (e) {
      if (version === generation.current) setError(explain(e));
    } finally {
      if (version === generation.current) setBusy(false);
    }
  }

  return (
    <section aria-label="Improvement campaigns">
      <h2>Search within a fixed budget</h2>
      <p>
        Generate alternatives, compare them with a simple model downgrade, then
        confirm one frozen candidate on untouched test groups.
      </p>
      <p>
        Every native generation, target and judge call shares one durable
        budget. Forecasts remain owner assumptions. Application deployment is a
        separate decision.
      </p>
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Loading campaigns…</p>}
      {canWrite && (
        <details
          open={prepareOpen}
          onToggle={(e) => setPrepareOpen(e.currentTarget.open)}
        >
          <summary>Prepare a bounded search</summary>
          <p>
            First prepare an application plan with authorized train, validation
            and test data, then freeze complete baseline evidence in Task
            economics. Preparation makes no provider calls.
          </p>
          <form onSubmit={prepare} className="grid">
            <p>
              Test the prepared model or output-cap alternative first. If it
              passes validation, skip instruction generation and confirm on
              untouched test groups. Otherwise use only the prompt-search effort
              that fits the same frozen budget. If no generation round fits,
              reject the failed alternative.
            </p>
            <label>
              Prepared application plan
              <select
                value={template}
                onChange={(e) => setTemplate(e.target.value)}
                required
              >
                <option value="">Select a prepared plan</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.workflow_label} · {t.baseline_model} ·{" "}
                    {t.source_bound ? "source bound" : "evaluation only"} ·{" "}
                    {t.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            {templateCursor && (
              <button
                type="button"
                disabled={busy}
                onClick={() => more("templates")}
              >
                More application plans
              </button>
            )}
            <label>
              Baseline environment
              <select
                value={environment}
                onChange={(e) => setEnvironment(e.target.value)}
                required
              >
                {environments.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Frozen baseline cost window
              <select
                value={snapshot}
                onChange={(e) => setSnapshot(e.target.value)}
                required
              >
                <option value="">Select complete baseline evidence</option>
                {snapshots.map((s) => (
                  <option
                    key={s.id}
                    value={s.id}
                    disabled={!s.summary.window_cost_complete_under_declaration}
                  >
                    {s.workflow} · {s.release} ·{" "}
                    {money(s.summary.declared_window_cost_per_success_usd)} per
                    success · {s.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            {snapshotCursor && (
              <button
                type="button"
                disabled={busy}
                onClick={() => more("snapshots")}
              >
                More cost windows
              </button>
            )}
            <label>
              Total spend ceiling (USD)
              <input
                name="usd"
                defaultValue="10"
                type="number"
                min="0"
                max="10000"
                step="any"
                required
              />
            </label>
            <label>
              Total request ceiling
              <input
                name="requests"
                defaultValue="500"
                type="number"
                min="1"
                max="10000"
                required
              />
            </label>
            <label>
              Campaign wall time (seconds)
              <input
                name="wall"
                defaultValue="300"
                type="number"
                min="1"
                max="3600"
                required
              />
            </label>
            <label>
              Maximum search rounds
              <input
                name="rounds"
                defaultValue="1"
                type="number"
                min="1"
                max="3"
                required
              />
            </label>
            <label>
              Generated variants per round
              <input
                name="variants"
                defaultValue="2"
                type="number"
                min="1"
                max="4"
                required
              />
            </label>
            <label>
              Conservative savings per successful task (USD)
              <input name="savings" type="number" min="0" step="any" />
            </label>
            <label>
              Forecast successful tasks
              <input name="volume" type="number" min="1" max="1000000000" />
            </label>
            <p>
              The collector spends at most 10% of this declared opportunity,
              within your ceiling. Unknown or insufficient opportunity causes
              abstention. The qualified proposer is gpt-4.1-mini; no credentials
              enter this browser.
            </p>
            <button disabled={busy || !template || !snapshot}>
              Prepare campaign without inference
            </button>
          </form>
        </details>
      )}
      {!loading && !rows.length && (
        <p>
          No campaigns yet. Prepare a source-bound workflow and baseline cost
          window, or follow the independent campaign example.
        </p>
      )}
      <div
        className="table-scroll"
        role="region"
        aria-label="Campaign list"
        tabIndex={0}
      >
        <table aria-label="Campaign budgets and progress">
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Status</th>
              <th>Requests</th>
              <th>Known modeled spend</th>
              <th>Reserved ceiling used</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>
                  <button disabled={busy} onClick={() => inspect(r.id)}>
                    Inspect {r.workflow_label} · {r.id.slice(0, 8)}
                  </button>
                </td>
                <td>{r.status}</td>
                <td>
                  {r.requests} reservations · {r.known_cost_requests} with known
                  cost
                </td>
                <td>
                  {money(r.known_spend_subtotal_usd)}
                  {!r.billing_complete && " · incomplete"}
                </td>
                <td>{money(r.reserved_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cursor && (
        <button disabled={busy} onClick={() => more("campaigns")}>
          More campaigns
        </button>
      )}
      {detail && (
        <section aria-label="Selected campaign">
          <h2 ref={selectedHeading} tabIndex={-1}>
            {detail.workflow_label} · {detail.id.slice(0, 8)}
          </h2>
          <p role="status">
            {detail.status} · {detail.requests} request reservations ·{" "}
            {detail.known_cost_requests} with known cost
          </p>
          {decisions[String(detail.summary.decision)] && (
            <p>{decisions[String(detail.summary.decision)]}</p>
          )}
          {detail.status === "CANCELLED" && (
            <p>
              New campaign calls have stopped. Late reported usage still belongs
              in this campaign's costs.
            </p>
          )}
          <p>
            Known modeled spend: {money(detail.known_spend_subtotal_usd)}.
            Cumulative pre-call reservations: {money(detail.reserved_usd)}.
            {!detail.billing_complete &&
              " Accounting is incomplete; known spend is a subtotal."}
          </p>
          <button disabled={busy} onClick={() => inspect(detail.id)}>
            Refresh campaign evidence
          </button>
          <h3>Frozen economic bounds</h3>
          {[
            "cutback.improvement-campaign.v2",
            "cutback.improvement-campaign.v3",
          ].includes(String(detail.specification.version)) && (
            <p>
              Search order:{" "}
              {detail.economic_envelope.static_alternative_available
                ? "static alternative, instruction generation only if needed, then independent confirmation."
                : "instruction generation, then independent confirmation. An unchanged control skips the duplicate static trial."}{" "}
              Initial validation reserves{" "}
              {String(detail.economic_envelope.initial_validation_requests)}{" "}
              requests.
              {detail.economic_envelope.instruction_search_enabled === false &&
                " No instruction-generation round fits this envelope."}
              Native API costs are bounded; other optimization overhead is
              unmeasured.
            </p>
          )}
          {detail.specification.version ===
            "cutback.improvement-campaign.v3" && (
            <div
              className="table-scroll"
              role="region"
              aria-label="Frozen static portfolio"
              tabIndex={0}
            >
              <table>
                <caption>
                  Model and output limits frozen before approval
                </caption>
                <thead>
                  <tr>
                    <th>Candidate</th>
                    <th>Model</th>
                    <th>Output token ceiling</th>
                    <th>Worst case USD per request</th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    (detail.economic_envelope.static_portfolio as Data[]) || []
                  ).map((m) => (
                    <tr key={String(m.key)}>
                      <td>{String(m.key)}</td>
                      <td>{String(m.model)}</td>
                      <td>{String(m.max_output_tokens)}</td>
                      <td>{gatewayModeledUSD(m.worst_case_request_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p>
                All listed alternatives share the same validation cases and
                parent budget. Only one eligible candidate consumes independent
                confirmation. If none passes, instruction search runs only when
                reserved in this envelope.
              </p>
              {Array.isArray(detail.economic_envelope.omitted_static_keys) &&
                detail.economic_envelope.omitted_static_keys.length > 0 && (
                  <p>
                    Preparation omitted{" "}
                    {detail.economic_envelope.omitted_static_keys.length}{" "}
                    alternatives to fit the reviewed budget. Validation and
                    confirmation samples were preserved.
                  </p>
                )}
            </div>
          )}
          <p>
            USD{" "}
            {String(
              detail.economic_envelope.effective_max_usd ?? "unknown",
            ).replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1")}{" "}
            total ceiling · {String(detail.economic_envelope.max_requests)}{" "}
            requests · {String(detail.economic_envelope.rounds)} rounds ·{" "}
            {String(detail.economic_envelope.variants_per_round)} generated
            variants per round
          </p>
          <p>
            {detail.workflow_label} · {detail.environment} ·{" "}
            {detail.max_wall_seconds} seconds maximum wall time.
          </p>
          <p>
            Proposer {detail.generator_model}. Approved providers:{" "}
            {detail.approved_providers.join(", ")}. Generated instructions are
            limited to {detail.max_instruction_bytes} UTF-8 bytes; request
            schemas, tools and destinations stay frozen.
          </p>
          <NativeContracts
            plan={nativeCampaignPlan(detail.specification)}
            planId={detail.id}
            context="campaign"
          />
          <p>
            Content authority expires{" "}
            {new Date(detail.expires_at).toLocaleString()}.{" "}
            {detail.source_profile_sha256
              ? "Bound to the prepared source profile."
              : "Evaluation only: source application checks still need a qualified binding."}
          </p>
          <p>
            {detail.instruction_edit_qualified
              ? "A literal instruction edit was prepared locally before approval. The agent rechecks the exact source before producing its diff and rollback."
              : "Instruction source edits need local adapter qualification before a generated instruction can become an application change."}
          </p>
          <p>
            Approval hash{" "}
            <code className="campaign-hash">{detail.campaign_hash}</code>
          </p>
          {detail.status === "ABSTAINED" && (
            <p>
              Search abstained:{" "}
              {JSON.stringify(detail.economic_envelope.reasons)}. No requests
              are authorized.
            </p>
          )}
          {canApprove && detail.status === "READY_FOR_APPROVAL" && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                decide("approve");
              }}
            >
              <label>
                Approval reference
                <input
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                  required
                  pattern="[a-zA-Z0-9_.:-]+"
                  maxLength={200}
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={approved}
                  onChange={(e) => setApproved(e.target.checked)}
                />
                I approve this exact campaign and its provider-call ceilings.
              </label>
              <p>
                Approval queues the campaign for an explicitly enabled operator
                worker. Its wall-time budget starts now.
              </p>
              <button disabled={busy || !approved || !reference}>
                Approve and queue this campaign
              </button>
            </form>
          )}
          {canWrite && active.has(detail.status) && (
            <button disabled={busy} onClick={() => decide("cancel")}>
              Cancel new campaign calls
            </button>
          )}
          <h3>Execution stages</h3>
          {searchDecisions[String(detail.summary.search_decision)] && (
            <p>{searchDecisions[String(detail.summary.search_decision)]}</p>
          )}
          {!detail.runs.length && <p>No child operation has started.</p>}
          <ol>
            {detail.runs.map((r, index) => (
              <li key={String(r.run_id)}>
                {index === 0 &&
                [
                  "cutback.improvement-campaign.v2",
                  "cutback.improvement-campaign.v3",
                ].includes(String(detail.specification.version)) &&
                detail.economic_envelope.static_alternative_available
                  ? "Initial model/output-cap validation"
                  : `Round ${String(r.round)} · ${String(r.phase)}`}{" "}
                · {String(r.status)} · {String(r.requests)} requests ·{" "}
                {money(r.known_spend_subtotal_usd)}
              </li>
            ))}
          </ol>
          <h3>Frozen candidate versions</h3>
          {canApprove && (
            <label>
              Purpose for reading a generated instruction
              <input
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                pattern="[a-zA-Z0-9_.:-]+"
                maxLength={200}
              />
            </label>
          )}
          <ul>
            {detail.candidates.map((c) => (
              <li key={String(c.id)}>
                {String(c.key)} · {String(c.kind)} · encrypted instruction
                retained under its capture policy
                {c.disposition === "duplicate_not_evaluated" &&
                  " · repeated configuration, evaluation skipped"}
                {canApprove && (
                  <button
                    disabled={busy || !/^[a-zA-Z0-9_.:-]{1,200}$/.test(purpose)}
                    onClick={() => readCandidate(String(c.id))}
                  >
                    Read {String(c.key)} instruction
                  </button>
                )}
              </li>
            ))}
          </ul>
          {content && (
            <section aria-label="Authorized candidate content">
              <h4>{content.configuration.key}</h4>
              <p>
                {content.configuration.model} · output cap{" "}
                {content.configuration.max_output_tokens}
              </p>
              <pre tabIndex={0}>{content.configuration.instruction}</pre>
              <details>
                <summary>Exact frozen request configuration</summary>
                <pre tabIndex={0}>
                  {JSON.stringify(content.configuration, null, 2)}
                </pre>
              </details>
              <button onClick={() => setContent(null)}>
                Clear candidate content from this view
              </button>
            </section>
          )}
          <details>
            <summary>Decision and evidence limits</summary>
            <pre tabIndex={0}>{JSON.stringify(detail.summary, null, 2)}</pre>
          </details>
        </section>
      )}
    </section>
  );
}
