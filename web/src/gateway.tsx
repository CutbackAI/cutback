import React, { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, Field, Select, explain, json } from "./ui";
import { gatewayModeledUSD as modeled } from "./gateway-money";

type Policy = components["schemas"]["GatewayPolicyReceipt"];
type Detail = components["schemas"]["GatewayPolicyDetail"];
type Key = components["schemas"]["GatewayKeyReceipt"];
type Call = components["schemas"]["GatewayCallReceipt"];
type PolicyInput = components["schemas"]["GatewayPolicy-Input"];
type KeyInput = components["schemas"]["GatewayKeyInput-Input"];
type LimitInput = components["schemas"]["SpendLimit-Input"];
type ModelInput = components["schemas"]["ModelLimit-Input"];
type RuntimePolicy = components["schemas"]["GuardrailControlReceipt"];
type Allowance = { dollars: string; requests: string; concurrent: string };
const initialAllowance = (): Allowance => ({
  dollars: "10",
  requests: "20",
  concurrent: "4",
});
const allowance = (value: Allowance): LimitInput => ({
  modeled_usd: value.dollars,
  requests: Number(value.requests),
  concurrent_requests: Number(value.concurrent),
});

function AllowanceFields({
  label,
  value,
  change,
}: {
  label: string;
  value: Allowance;
  change: (value: Allowance) => void;
}) {
  return (
    <fieldset>
      <legend>{label} limits</legend>
      <Field
        label={`${label} modeled USD`}
        name={`${label}-usd`}
        required
        inputMode="decimal"
        value={value.dollars}
        pattern="(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,12})?"
        onChange={(e) => change({ ...value, dollars: e.target.value })}
      />
      <Field
        label={`${label} total requests`}
        name={`${label}-requests`}
        required
        type="number"
        min="1"
        max="100000"
        value={value.requests}
        onChange={(e) => change({ ...value, requests: e.target.value })}
      />
      <Field
        label={`${label} active gateway requests`}
        name={`${label}-concurrent`}
        required
        type="number"
        min="1"
        max="100"
        value={value.concurrent}
        onChange={(e) => change({ ...value, concurrent: e.target.value })}
      />
    </fieldset>
  );
}

export function GatewayBudgets({
  api,
  project,
  canApprove,
}: {
  api: API;
  project: string;
  canApprove: boolean;
}) {
  const base = `/projects/${project}/gateway`;
  const [environments, setEnvironments] = useState<
    { id: string; name: string }[]
  >([]);
  const [selectedEnvironments, setSelectedEnvironments] = useState<string[]>(
    [],
  );
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [keys, setKeys] = useState<Key[]>([]);
  const [calls, setCalls] = useState<Call[]>([]);
  const [cursors, setCursors] = useState<Record<string, string | null>>({});
  const [detail, setDetail] = useState<Detail | null>(null);
  const [priceHash, setPriceHash] = useState("");
  const [projectLimit, setProjectLimit] = useState(initialAllowance);
  const [modelLimit, setModelLimit] = useState(initialAllowance);
  const [teams, setTeams] = useState(() => [
    { id: crypto.randomUUID(), limit: initialAllowance() },
  ]);
  const [hours, setHours] = useState("24");
  const [effort, setEffort] = useState("medium");
  const [output, setOutput] = useState("1024");
  const [timeout, setTimeout] = useState("60");
  const [streaming, setStreaming] = useState("disabled");
  const [runtimePolicies, setRuntimePolicies] = useState<RuntimePolicy[]>([]);
  const [runtimePolicy, setRuntimePolicy] = useState("");
  const [keyLimit, setKeyLimit] = useState(initialAllowance);
  const [keyTeam, setKeyTeam] = useState("");
  const [keyEnvironment, setKeyEnvironment] = useState("");
  const [keyPurpose, setKeyPurpose] = useState("application");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const detailGeneration = useRef(0);
  const prepared = useRef<PolicyInput | null>(null);
  const keyIntent = useRef<KeyInput | null>(null);
  const approvalRefs = useRef(
    new Map<string, { reference: string; version: number }>(),
  );
  const stopVersions = useRef(new Map<string, number>());
  const [frozenDraft, setFrozenDraft] = useState(false);

  const reload = useCallback(async () => {
    const epoch = generation.current;
    const [environmentPage, policyPage, keyPage, callPage, prices, guardrails] =
      await Promise.all([
        api(`/projects/${project}/environments`),
        api(base + "/policies"),
        api(base + "/keys"),
        api(base + "/calls"),
        api(`/projects/${project}/prices`),
        api(`/projects/${project}/runtime-guardrails?limit=100`),
      ]);
    if (generation.current !== epoch) return;
    setEnvironments(environmentPage.items);
    setSelectedEnvironments((current) =>
      current.length
        ? current
        : environmentPage.items
            .slice(0, 1)
            .map((value: { id: string }) => value.id),
    );
    setPolicies(policyPage.items);
    setKeys(keyPage.items);
    setCalls(callPage.items);
    setRuntimePolicies(
      guardrails.items.filter(
        (policy: RuntimePolicy) =>
          policy.state === "APPROVED" &&
          policy.execution_authority === "current",
      ),
    );
    setCursors({
      policies: policyPage.next_after,
      keys: keyPage.next_after,
      calls: callPage.next_after,
    });
    const snapshot = prices.observation_catalogs?.find(
      (value: { snapshot_id: string }) =>
        value.snapshot_id === "openai-text-2026-10-28-v1",
    );
    setPriceHash(typeof snapshot?.sha256 === "string" ? snapshot.sha256 : "");
  }, [api, base, project]);

  useEffect(() => {
    generation.current++;
    setSecret("");
    setBusy(true);
    setError("");
    const epoch = generation.current;
    reload()
      .catch((e) => {
        if (epoch === generation.current) setError(explain(e));
      })
      .finally(() => {
        if (epoch === generation.current) setBusy(false);
      });
    return () => {
      generation.current++;
      detailGeneration.current++;
    };
  }, [reload]);
  useEffect(() => {
    setSecret("");
    keyIntent.current = null;
  }, [canApprove]);

  async function inspect(id: string) {
    const epoch = generation.current,
      request = ++detailGeneration.current;
    const value = (await api(`${base}/policies/${id}`)) as Detail;
    if (epoch !== generation.current || request !== detailGeneration.current)
      return;
    setDetail(value);
    setSecret("");
    keyIntent.current = null;
    setKeyTeam(value.policy.specification.teams[0]?.team_id || "");
    setKeyEnvironment(value.policy.specification.environment_ids[0] || "");
    setKeyPurpose("application");
    const limit = value.policy.specification.project_limit;
    setKeyLimit({
      dollars: String(limit.modeled_usd),
      requests: String(limit.requests),
      concurrent: String(limit.concurrent_requests),
    });
  }

  async function act(work: () => Promise<void>) {
    if (busy) return;
    const epoch = generation.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      if (epoch === generation.current) setError(explain(e));
    } finally {
      if (epoch === generation.current) setBusy(false);
    }
  }

  async function prepare(event: React.FormEvent) {
    event.preventDefault();
    await act(async () => {
      if (!prepared.current) {
        const starts = new Date();
        const selectedGuard = runtimePolicies.find(
          (policy) => policy.id === runtimePolicy,
        );
        if (runtimePolicy && !selectedGuard)
          throw new Error("GATEWAY_GUARDRAIL_APPROVAL_NOT_CURRENT");
        prepared.current = {
          version: selectedGuard
            ? "cutback.gateway-policy.v3"
            : streaming === "enabled"
              ? "cutback.gateway-policy.v2"
              : "cutback.gateway-policy.v1",
          policy_id: crypto.randomUUID(),
          environment_ids: selectedEnvironments,
          starts_at: starts.toISOString(),
          expires_at: new Date(
            Math.min(
              starts.getTime() + Number(hours) * 3600000,
              selectedGuard
                ? Date.parse(selectedGuard.binding.expires_at)
                : Infinity,
            ),
          ).toISOString(),
          ...(selectedGuard
            ? { runtime_guardrails: selectedGuard.binding }
            : {}),
          project_limit: allowance(projectLimit),
          teams: teams.map((team) => ({
            team_id: team.id,
            limit: allowance(team.limit),
          })),
          models: [
            {
              key:
                streaming === "enabled"
                  ? "openai-luna-responses-text-stream-v1"
                  : "openai-luna-responses-text-v1",
              provider: "openai",
              model: "gpt-6-luna",
              sdk_surface: "responses.create",
              reservation_contract: "openai-luna-full-context-fast-reserve-v1",
              context_tokens: 1050000,
              max_output_tokens: Number(output),
              timeout_seconds: Number(timeout),
              limit: allowance(modelLimit),
              price_contract: {
                version: "openai-luna-responses-text-v1",
                price_snapshot_id: "openai-text-2026-10-28-v1",
                price_sha256: priceHash,
                service_tier: "default",
                cache_write_lifetime: "30m",
                reasoning_effort:
                  effort as ModelInput["price_contract"]["reasoning_effort"],
              },
            },
          ],
        };
        setFrozenDraft(true);
      }
      const epoch = generation.current;
      const row = (await json(
        api,
        base + "/policies",
        prepared.current,
      )) as Policy;
      if (epoch !== generation.current) return;
      prepared.current = null;
      setFrozenDraft(false);
      await reload();
      await inspect(row.id);
      setNotice(
        "Policy prepared. Review its exact scopes, limits, window and hash before approval.",
      );
    });
  }

  async function approve() {
    if (!detail) return;
    const policy = detail.policy;
    await act(async () => {
      let intent = approvalRefs.current.get(policy.id);
      if (!intent) {
        intent = {
          reference: crypto.randomUUID(),
          version: policy.state_version,
        };
        approvalRefs.current.set(policy.id, intent);
      }
      try {
        await json(api, `${base}/policies/${policy.id}/approve`, {
          approval_hash: policy.approval_hash,
          approval_ref: intent.reference,
          expected_state_version: intent.version,
        });
      } catch (error) {
        if (
          (error as Error)?.message ===
          "GATEWAY_STATE_VERSION_CONFLICT_REVIEW_CURRENT_POLICY"
        ) {
          approvalRefs.current.delete(policy.id);
          await inspect(policy.id);
        }
        throw error;
      }
      await reload();
      await inspect(policy.id);
      setNotice(
        "Policy approved. This does not start a gateway or configure a provider credential.",
      );
    });
  }

  async function issue(event: React.FormEvent) {
    event.preventDefault();
    if (!detail) return;
    const policy = detail.policy,
      epoch = generation.current;
    await act(async () => {
      if (!keyIntent.current)
        keyIntent.current = {
          request_id: crypto.randomUUID(),
          policy_id: policy.id,
          environment_id: keyEnvironment,
          team_id: keyTeam,
          expires_at: policy.specification.expires_at,
          traffic_kind: keyPurpose as KeyInput["traffic_kind"],
          limit: allowance(keyLimit),
          model_keys: policy.specification.models.map((model) => model.key),
        };
      const issued = await json(api, base + "/keys", keyIntent.current);
      if (epoch !== generation.current) return;
      keyIntent.current = null;
      await reload();
      await inspect(policy.id);
      setSecret(issued.token);
      setNotice(
        `Gateway key created for ${issued.key.specification.traffic_kind} traffic. It is displayed once and remains only in this page's memory.`,
      );
    });
  }

  async function more(kind: "policies" | "keys" | "calls") {
    const cursor = cursors[kind];
    if (!cursor) return;
    await act(async () => {
      const epoch = generation.current;
      const page = await api(
        `${base}/${kind}?after=${encodeURIComponent(cursor)}&limit=50`,
      );
      if (epoch !== generation.current) return;
      if (kind === "policies") setPolicies((old) => [...old, ...page.items]);
      if (kind === "keys") setKeys((old) => [...old, ...page.items]);
      if (kind === "calls") setCalls((old) => [...old, ...page.items]);
      setCursors((old) => ({ ...old, [kind]: page.next_after }));
    });
  }

  return (
    <div>
      <h2>Control application traffic</h2>
      <p>
        Share a finite allowance across application keys, teams and models.
        Limits use a frozen public list-price model; they do not guarantee an
        invoice amount. Provider secrets belong to a separately enabled gateway
        process and never enter this dashboard.
      </p>
      <p>
        Native synchronous and asynchronous Responses text supports optional
        HTTP SSE under a separately reviewed policy. It reserves the complete
        supported context and output ceiling before dispatch. Unknown usage
        keeps its reservation. Reusing an operation ID cannot repeat a billable
        call.
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <button disabled={busy} onClick={() => act(reload)}>
        Refresh budget records
      </button>
      {busy && <p role="status">Loading or saving budget records…</p>}

      <section aria-label="Gateway policies">
        <h3>Finite policies</h3>
        {!policies.length ? (
          <p className="empty">
            No gateway policy has been prepared for this project.
          </p>
        ) : (
          <div className="table-scroll" tabIndex={0}>
            <table className="gateway-table">
              <thead>
                <tr>
                  <th>Policy</th>
                  <th>State</th>
                  <th>Window (UTC)</th>
                  <th>Project allowance</th>
                  <th>Review</th>
                </tr>
              </thead>
              <tbody>
                {policies.map((policy) => (
                  <tr key={policy.id}>
                    <th scope="row">
                      <code>{policy.id}</code>
                    </th>
                    <td>{policy.state}</td>
                    <td>
                      {policy.specification.starts_at}
                      <br />
                      to {policy.specification.expires_at}
                    </td>
                    <td>
                      {modeled(policy.specification.project_limit.modeled_usd)}{" "}
                      modeled
                      <br />
                      {policy.specification.project_limit.requests} total
                      requests
                    </td>
                    <td>
                      <button
                        disabled={busy}
                        onClick={() => act(() => inspect(policy.id))}
                      >
                        Inspect policy {policy.id.slice(0, 8)}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {cursors.policies && (
          <button disabled={busy} onClick={() => more("policies")}>
            More policies
          </button>
        )}
      </section>

      {detail && (
        <section aria-label="Reviewed gateway policy">
          <h3>Policy review</h3>
          <p>
            <code>{detail.policy.id}</code> · {detail.policy.state}
          </p>
          <p>
            Approval SHA-256: <code>{detail.policy.approval_hash}</code>
          </p>
          <p>
            Expires {detail.policy.specification.expires_at}. Environments:{" "}
            {detail.policy.specification.environment_ids
              .map(
                (id) =>
                  environments.find((environment) => environment.id === id)
                    ?.name || id,
              )
              .join(", ")}
            .
          </p>
          {detail.policy.specification.models.map((model) => (
            <p key={model.key}>
              {model.key === "openai-luna-responses-text-stream-v1"
                ? "HTTP SSE and nonstreaming text"
                : "Nonstreaming text only"}{" "}
              · {model.model} · reasoning{" "}
              {model.price_contract.reasoning_effort} · requested tier default ·
              output ceiling {model.max_output_tokens} tokens · timeout{" "}
              {model.timeout_seconds}
              s.
              <br />
              Price SHA-256: <code>{model.price_contract.price_sha256}</code>
              <br />
              Currently qualified per-call reservation:{" "}
              {modeled(detail.policy.qualified_reservations_usd[model.key])}.
              This is a budget hold, not an invoice prediction.
            </p>
          ))}
          <p>
            Every call needs a stable operation UUID. There is no automatic
            provider retry, fallback or saved-response replay. Already
            dispatched work cannot be recalled by disabling a policy.
          </p>
          {canApprove && detail.policy.state === "PENDING" && (
            <button disabled={busy} onClick={approve}>
              Approve this exact gateway policy
            </button>
          )}
          {canApprove &&
            ["PENDING", "APPROVED", "DISABLED", "BLOCKED"].includes(
              detail.policy.state,
            ) && (
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const id = detail.policy.id;
                    let version = stopVersions.current.get(id);
                    if (version === undefined) {
                      version = detail.policy.state_version;
                      stopVersions.current.set(id, version);
                    }
                    try {
                      await json(api, `${base}/policies/${id}/disable`, {
                        approval_hash: detail.policy.approval_hash,
                        expected_state_version: version,
                      });
                    } catch (error) {
                      if (
                        (error as Error)?.message ===
                        "GATEWAY_STATE_VERSION_CONFLICT_REVIEW_CURRENT_POLICY"
                      ) {
                        stopVersions.current.delete(id);
                        await inspect(id);
                      }
                      throw error;
                    }
                    stopVersions.current.delete(id);
                    approvalRefs.current.delete("resume:" + detail.policy.id);
                    await reload();
                    await inspect(detail.policy.id);
                  })
                }
              >
                Disable policy for new calls
              </button>
            )}
          {canApprove &&
            detail.policy.approved_at &&
            ["DISABLED", "BLOCKED"].includes(detail.policy.state) && (
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const key = "resume:" + detail.policy.id;
                    let intent = approvalRefs.current.get(key);
                    if (!intent) {
                      intent = {
                        reference: crypto.randomUUID(),
                        version: detail.policy.state_version,
                      };
                      approvalRefs.current.set(key, intent);
                    }
                    try {
                      await json(
                        api,
                        `${base}/policies/${detail.policy.id}/resume`,
                        {
                          approval_hash: detail.policy.approval_hash,
                          approval_ref: intent.reference,
                          expected_state_version: intent.version,
                        },
                      );
                    } catch (error) {
                      if (
                        [
                          "GATEWAY_ACTIVATION_APPROVAL_ALREADY_CONSUMED",
                          "GATEWAY_STATE_VERSION_CONFLICT_REVIEW_CURRENT_POLICY",
                        ].includes((error as Error)?.message)
                      ) {
                        approvalRefs.current.delete(key);
                        await inspect(detail.policy.id);
                      }
                      throw error;
                    }
                    approvalRefs.current.delete(key);
                    await reload();
                    await inspect(detail.policy.id);
                    setNotice(
                      "Policy resumed with its original window, used requests and retained reservations. No prior operation is retried.",
                    );
                  })
                }
              >
                Resume with retained limits
              </button>
            )}
          <details>
            <summary>Exact frozen policy</summary>
            <pre>{JSON.stringify(detail.policy.specification, null, 2)}</pre>
          </details>
          <h4>Shared accounting</h4>
          <p>
            Known spend excludes uncertain calls; reservations are not charges.
            The complete modeled total stays unknown while a request is active
            or uncertain. Scroll the table horizontally to inspect every column
            on a small screen.
          </p>
          {!detail.budgets.length ? (
            <p>No calls are authorized until this policy is approved.</p>
          ) : (
            <div className="table-scroll" tabIndex={0}>
              <table className="gateway-table">
                <thead>
                  <tr>
                    <th>Scope</th>
                    <th>Known modeled spend</th>
                    <th>Reservations</th>
                    <th>Remaining</th>
                    <th>Requests used</th>
                    <th>Active / uncertain</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.budgets.map((budget) => (
                    <tr key={`${budget.scope_kind}:${budget.scope_key}`}>
                      <th scope="row">
                        {budget.scope_kind}
                        <br />
                        <code>{budget.scope_key}</code>
                      </th>
                      <td>{modeled(budget.known_modeled_spent_usd)}</td>
                      <td>{modeled(budget.retained_reservations_usd)}</td>
                      <td>{modeled(budget.remaining_modeled_usd)}</td>
                      <td>
                        {budget.requests_used} / {budget.limit.requests}
                      </td>
                      <td>
                        {budget.active_requests} active /{" "}
                        {budget.uncertain_requests} uncertain
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {canApprove && detail.policy.state === "APPROVED" && (
            <form onSubmit={issue}>
              <h4>Create an application key</h4>
              <fieldset disabled={busy}>
                <Select
                  label="Key environment"
                  name="key-environment"
                  value={keyEnvironment}
                  onChange={(e) => {
                    keyIntent.current = null;
                    setKeyEnvironment(e.target.value);
                  }}
                >
                  {detail.policy.specification.environment_ids.map((id) => (
                    <option key={id} value={id}>
                      {environments.find((environment) => environment.id === id)
                        ?.name || id}
                    </option>
                  ))}
                </Select>
                <Select
                  label="Key team"
                  name="key-team"
                  value={keyTeam}
                  onChange={(e) => {
                    keyIntent.current = null;
                    setKeyTeam(e.target.value);
                  }}
                >
                  {detail.policy.specification.teams.map((team) => (
                    <option key={team.team_id} value={team.team_id}>
                      {team.team_id}
                    </option>
                  ))}
                </Select>
                <Select
                  label="Key traffic purpose"
                  name="key-traffic-purpose"
                  value={keyPurpose}
                  onChange={(event) => {
                    keyIntent.current = null;
                    setKeyPurpose(event.target.value);
                  }}
                >
                  {[
                    "application",
                    "evaluation",
                    "judge",
                    "optimization",
                    "simulation",
                    "unknown",
                  ].map((purpose) => (
                    <option key={purpose} value={purpose}>
                      {purpose}
                    </option>
                  ))}
                </Select>
                <p>
                  The key's frozen purpose separates application calls from
                  evaluation and optimizer overhead.
                </p>
                <AllowanceFields
                  label="Key"
                  value={keyLimit}
                  change={(value) => {
                    keyIntent.current = null;
                    setKeyLimit(value);
                  }}
                />
                <p>
                  Key limits must fit the approved project, team and model.
                  Expiry is the policy's expiry.
                </p>
                <button type="submit">Create gateway key</button>
              </fieldset>
            </form>
          )}
          {secret && (
            <div role="region" aria-label="New gateway credential">
              <p>
                Copy this key before leaving. It cannot be recovered from its
                stored digest.
              </p>
              <output aria-label="Gateway key displayed once">
                <code>{secret}</code>
              </output>
              <button onClick={() => setSecret("")}>Hide gateway key</button>
            </div>
          )}
        </section>
      )}

      {canApprove && (
        <section aria-label="Prepare a gateway policy">
          <h3>Prepare a policy</h3>
          <p>
            This prepares an immutable intent. Approval is a separate action.
            Approved windows cannot overlap, including after disabling an older
            policy; limits cannot be reset during a window.
          </p>
          {!priceHash && (
            <p>
              No qualified bundled modern-text price reference is available on
              this collector.
            </p>
          )}
          <form onSubmit={prepare}>
            <fieldset disabled={busy || frozenDraft || !priceHash}>
              <fieldset>
                <legend>Authorized environments</legend>
                {environments.map((environment) => (
                  <label className="checkbox" key={environment.id}>
                    <input
                      type="checkbox"
                      checked={selectedEnvironments.includes(environment.id)}
                      onChange={(event) => {
                        setSelectedEnvironments((old) =>
                          event.target.checked
                            ? [...old, environment.id]
                            : old.filter((id) => id !== environment.id),
                        );
                      }}
                    />
                    {environment.name}
                  </label>
                ))}
              </fieldset>
              <Field
                label="Policy duration in hours"
                name="policy-hours"
                type="number"
                required
                min="1"
                max="744"
                value={hours}
                onChange={(e) => setHours(e.target.value)}
              />
              <AllowanceFields
                label="Project"
                value={projectLimit}
                change={setProjectLimit}
              />
              <h4>Teams</h4>
              <p>
                Each application key belongs to one of these opaque team IDs.
              </p>
              {teams.map((team, index) => (
                <div key={team.id}>
                  <p>
                    Team {index + 1}: <code>{team.id}</code>
                  </p>
                  <AllowanceFields
                    label={`Team ${index + 1}`}
                    value={team.limit}
                    change={(value) =>
                      setTeams((old) =>
                        old.map((item) =>
                          item.id === team.id
                            ? { ...item, limit: value }
                            : item,
                        ),
                      )
                    }
                  />
                  {teams.length > 1 && (
                    <button
                      type="button"
                      onClick={() =>
                        setTeams((old) =>
                          old.filter((item) => item.id !== team.id),
                        )
                      }
                    >
                      Remove team {index + 1}
                    </button>
                  )}
                </div>
              ))}
              <button
                type="button"
                disabled={teams.length >= 100}
                onClick={() =>
                  setTeams((old) => [
                    ...old,
                    { id: crypto.randomUUID(), limit: initialAllowance() },
                  ])
                }
              >
                Add team
              </button>
              <h4>Qualified model</h4>
              <p>
                gpt-6-luna · Responses text · explicit default tier · 30m cache
                option.
              </p>
              <Select
                label="Native text streaming"
                name="model-streaming"
                value={streaming}
                onChange={(e) => setStreaming(e.target.value)}
              >
                <option value="disabled">Disabled · nonstreaming only</option>
                <option value="enabled">
                  Allow HTTP SSE and nonstreaming text
                </option>
              </Select>
              <Select
                label="Runtime input and output policy"
                name="runtime-guardrail-policy"
                value={runtimePolicy}
                onChange={(e) => {
                  setRuntimePolicy(e.target.value);
                  const selected = runtimePolicies.find(
                    (policy) => policy.id === e.target.value,
                  );
                  if (selected)
                    setSelectedEnvironments(
                      environments
                        .filter(
                          (env) => env.name === selected.binding.environment,
                        )
                        .map((env) => env.id),
                    );
                }}
              >
                <option value="">No runtime guardrails</option>
                {runtimePolicies.map((policy) => (
                  <option key={policy.id} value={policy.id}>
                    {policy.id} · {policy.binding.on_unavailable}
                  </option>
                ))}
              </Select>
              <p>
                Register and approve rules in Runtime guardrails first. A
                guarded policy ends no later than those rules, requires a
                separately configured checker, and buffers SSE before delivering
                any content. Unknown check coverage remains visible.
              </p>
              <p>
                Interrupted streams keep their budget hold when final usage is
                unknown. Streaming and nonstreaming calls share the same model
                allowance.
              </p>
              <Select
                label="Model reasoning effort"
                name="model-reasoning"
                value={effort}
                onChange={(e) => setEffort(e.target.value)}
              >
                {["none", "low", "medium", "high", "xhigh", "max"].map(
                  (value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ),
                )}
              </Select>
              <Field
                label="Model output ceiling in tokens"
                name="model-output"
                type="number"
                required
                min="16"
                max="16384"
                value={output}
                onChange={(e) => setOutput(e.target.value)}
              />
              <Field
                label="Model timeout in seconds"
                name="model-timeout"
                type="number"
                required
                min="1"
                max="120"
                value={timeout}
                onChange={(e) => setTimeout(e.target.value)}
              />
              <AllowanceFields
                label="Model"
                value={modelLimit}
                change={setModelLimit}
              />
              <p>
                Team and model limits must fit the project limits. USD limits
                must cover at least one full-context reservation; this is
                deliberately more conservative than an input-length estimate.
              </p>
            </fieldset>
            <button
              type="submit"
              disabled={busy || !priceHash || !selectedEnvironments.length}
            >
              {frozenDraft
                ? "Retry this prepared intent"
                : "Prepare gateway policy"}
            </button>
            {frozenDraft && (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  prepared.current = null;
                  setFrozenDraft(false);
                }}
              >
                Discard local intent after inspecting saved policies
              </button>
            )}
          </form>
        </section>
      )}

      <section aria-label="Gateway key records">
        <h3>Application keys</h3>
        {!keys.length ? (
          <p className="empty">No gateway key has been issued.</p>
        ) : (
          <div className="table-scroll" tabIndex={0}>
            <table className="gateway-table">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Team</th>
                  <th>Environment</th>
                  <th>State</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => (
                  <tr key={key.id}>
                    <th scope="row">
                      <code>{key.id}</code>
                    </th>
                    <td>
                      <code>{key.team_id}</code>
                    </td>
                    <td>
                      {environments.find(
                        (environment) => environment.id === key.environment_id,
                      )?.name || key.environment_id}
                    </td>
                    <td>
                      {key.revoked_at
                        ? "Revoked"
                        : new Date(key.expires_at) <= new Date()
                          ? "Expired"
                          : "Issued"}
                    </td>
                    <td>
                      {canApprove && !key.revoked_at && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            act(async () => {
                              await api(`${base}/keys/${key.id}`, {
                                method: "DELETE",
                              });
                              setSecret("");
                              await reload();
                            })
                          }
                        >
                          Revoke key {key.id.slice(0, 8)}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {cursors.keys && (
          <button disabled={busy} onClick={() => more("keys")}>
            More gateway keys
          </button>
        )}
      </section>

      <section aria-label="Gateway call records">
        <h3>Dispatch and accounting receipts</h3>
        <p>
          Receipts contain metadata only. Request and response payloads are not
          saved for replay. First-text timing measures the gateway's wait for
          its first nonempty text delta, excluding the customer's network.
          Unknown timing stays unknown.
        </p>
        {!calls.length ? (
          <p className="empty">
            No application call has been dispatched through this gateway.
          </p>
        ) : (
          <div className="table-scroll" tabIndex={0}>
            <table className="gateway-table">
              <thead>
                <tr>
                  <th>Operation</th>
                  <th>State</th>
                  <th>Traffic purpose</th>
                  <th>Transport</th>
                  <th>Gateway first text (ms)</th>
                  <th>Original reservation</th>
                  <th>Known modeled cost</th>
                  <th>Code</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((call) => (
                  <tr key={call.operation_id}>
                    <th scope="row">
                      <code>{call.operation_id}</code>
                    </th>
                    <td>{call.state}</td>
                    <td>{call.traffic_kind}</td>
                    <td>
                      {call.streaming === null
                        ? "Unknown"
                        : call.streaming
                          ? "HTTP SSE"
                          : "JSON"}
                    </td>
                    <td>
                      {call.first_text_delta_us === null
                        ? "Unknown"
                        : (call.first_text_delta_us / 1000).toFixed(3)}
                    </td>
                    <td>{modeled(call.reserved_usd)}</td>
                    <td>{modeled(call.cost_usd)}</td>
                    <td>{call.code || "Recorded"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {cursors.calls && (
          <button disabled={busy} onClick={() => more("calls")}>
            More gateway calls
          </button>
        )}
      </section>
    </div>
  );
}
