import React, { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, explain, json } from "./ui";

type Policy = components["schemas"]["GuardrailControlReceipt"];
type Operation = components["schemas"]["GuardrailOperationReceipt"];

export function RuntimeGuardrails({
  api,
  project,
  canApprove,
}: {
  api: API;
  project: string;
  canApprove: boolean;
}) {
  const base = `/projects/${project}/runtime-guardrails`;
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [detail, setDetail] = useState<Policy | null>(null);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [operationCursor, setOperationCursor] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [privatePolicy, setPrivatePolicy] = useState<unknown>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0),
    selection = useRef(0);
  const approvalRefs = useRef(new Map<string, string>());
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const epoch = generation.current;
    const page = await api(base);
    if (epoch !== generation.current) return;
    setPolicies(page.items);
    setNext(page.next_after);
  }, [api, base]);

  useEffect(() => {
    const epoch = ++generation.current;
    setPolicies([]);
    setDetail(null);
    setPrivatePolicy(null);
    setOperations([]);
    setFile(null);
    setConsent(false);
    setReviewed(false);
    setError("");
    load().catch((e) => {
      if (epoch === generation.current) setError(explain(e));
    });
    return () => {
      generation.current++;
      selection.current++;
    };
  }, [load]);
  useEffect(() => {
    selection.current++;
    setPrivatePolicy(null);
    setReviewed(false);
    setFile(null);
    setConsent(false);
  }, [canApprove]);

  async function act(work: () => Promise<void>) {
    if (busy) return;
    const epoch = generation.current;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      if (epoch === generation.current) setError(explain(e));
    } finally {
      if (epoch === generation.current) setBusy(false);
    }
  }

  async function inspect(id: string) {
    const epoch = generation.current,
      selected = ++selection.current;
    setPrivatePolicy(null);
    setReviewed(false);
    setDetail(null);
    setOperations([]);
    const [policy, page] = await Promise.all([
      api(`${base}/${id}`),
      api(`${base}/${id}/operations`),
    ]);
    if (epoch !== generation.current || selected !== selection.current) return;
    setDetail(policy);
    setOperations(page.items);
    setOperationCursor(page.next_after);
  }

  return (
    <div>
      <h2>Runtime guardrails</h2>
      <p>
        Check input before dispatch and quarantine output before delivery. A
        denial after a model or tool ran cannot undo its cost or state changes.
      </p>
      <p>
        These policies enforce literal denies, bounded JSON structure and named
        tool allowlists. Semantic safety, encoded attacks and classifier
        false-positive rates are not established by these checks.
      </p>
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">Loading or saving guardrail records…</p>}
      <button disabled={busy} onClick={() => act(load)}>
        Refresh guardrail policies
      </button>
      <h3>Policies</h3>
      {!policies.length ? (
        <p>
          No runtime policy is registered. Try{" "}
          <code>cutback guardrails demo</code> locally, then register a reviewed
          policy for this project.
        </p>
      ) : (
        <div className="table-scroll" tabIndex={0}>
          <table>
            <thead>
              <tr>
                <th>Policy</th>
                <th>State</th>
                <th>Execution authority</th>
                <th>Unavailable checks</th>
                <th>Shared checks used</th>
                <th>Review</th>
              </tr>
            </thead>
            <tbody>
              {policies.map((policy) => (
                <tr key={policy.id}>
                  <td>
                    <code>{policy.id}</code>
                  </td>
                  <td>{policy.state}</td>
                  <td>{policy.execution_authority}</td>
                  <td>
                    {policy.binding.on_unavailable === "fail_open"
                      ? "Allow with unknown coverage"
                      : "Deny delivery"}
                  </td>
                  <td>
                    {policy.checks_used} / {policy.max_checks}
                  </td>
                  <td>
                    <button
                      disabled={busy}
                      onClick={() => act(() => inspect(policy.id))}
                    >
                      Review policy {policy.id.slice(0, 8)}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {next && (
        <button
          disabled={busy}
          onClick={() =>
            act(async () => {
              const epoch = generation.current,
                page = await api(`${base}?after=${encodeURIComponent(next)}`);
              if (epoch !== generation.current) return;
              setPolicies((old) => [...old, ...page.items]);
              setNext(page.next_after);
            })
          }
        >
          Load more guardrail policies
        </button>
      )}

      {detail && (
        <section aria-label="Selected runtime policy">
          <h3>Review current authority</h3>
          <dl>
            <dt>Policy</dt>
            <dd>
              <code>{detail.id}</code>
            </dd>
            <dt>Environment and workflows</dt>
            <dd>
              {detail.binding.environment}:{" "}
              {detail.binding.workflows.join(", ")}
            </dd>
            <dt>Authorized window</dt>
            <dd>
              {detail.binding.starts_at} → {detail.binding.expires_at}
            </dd>
            <dt>Protected stages</dt>
            <dd>{detail.binding.protected_stages.join(", ")}</dd>
            <dt>Exact approval hash</dt>
            <dd>
              <code>{detail.approval_hash}</code>
            </dd>
          </dl>
          <p>
            Checker credentials and endpoint configuration belong to the
            separately operated runtime process. Approval alone does not connect
            an application or deploy a gateway.
          </p>
          {canApprove && (
            <>
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const epoch = generation.current,
                      selected = selection.current;
                    const value = await json(
                      api,
                      `${base}/${detail.id}/content`,
                      { purpose: "runtime-policy-review" },
                    );
                    if (
                      epoch === generation.current &&
                      selected === selection.current
                    )
                      setPrivatePolicy(value);
                  })
                }
              >
                Read private policy for review
              </button>
              {privatePolicy !== null && (
                <>
                  <button onClick={() => setPrivatePolicy(null)}>
                    Hide private policy
                  </button>
                  <pre aria-label="Private runtime policy">
                    {JSON.stringify(privatePolicy, null, 2)}
                  </pre>
                </>
              )}
              {detail.state === "PENDING" && (
                <>
                  <label>
                    <input
                      type="checkbox"
                      checked={reviewed}
                      onChange={(e) => setReviewed(e.target.checked)}
                    />{" "}
                    I reviewed the private rules, scope, expiry, shared check
                    limit and unavailable-check behavior.
                  </label>
                  <button
                    disabled={busy || !reviewed}
                    onClick={() =>
                      act(async () => {
                        let reference = approvalRefs.current.get(detail.id);
                        if (!reference) {
                          reference = crypto.randomUUID();
                          approvalRefs.current.set(detail.id, reference);
                        }
                        await json(api, `${base}/${detail.id}/approve`, {
                          approval_hash: detail.approval_hash,
                          approval_ref: reference,
                          expected_state_version: detail.state_version,
                        });
                        await load();
                        await inspect(detail.id);
                      })
                    }
                  >
                    Approve this exact runtime policy
                  </button>
                </>
              )}
              {["PENDING", "APPROVED"].includes(detail.state) && (
                <button
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      await json(api, `${base}/${detail.id}/disable`, {
                        approval_hash: detail.approval_hash,
                        expected_state_version: detail.state_version,
                      });
                      await load();
                      await inspect(detail.id);
                    })
                  }
                >
                  Disable this runtime policy
                </button>
              )}
            </>
          )}
          <h3>Check receipts</h3>
          <p>
            A missing or reserved receipt has unknown coverage. These records do
            not establish application quality or a zero-dollar call.
          </p>
          {!operations.length ? (
            <p>No checks have been reserved for this policy.</p>
          ) : (
            <div className="table-scroll" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th>Operation</th>
                    <th>Stage</th>
                    <th>State</th>
                    <th>Decision</th>
                    <th>Coverage</th>
                  </tr>
                </thead>
                <tbody>
                  {operations.map((op) => (
                    <tr key={`${op.operation_id}:${op.stage}`}>
                      <td>
                        <code>{op.operation_id}</code>
                      </td>
                      <td>{op.stage}</td>
                      <td>{op.state}</td>
                      <td>
                        {op.decision
                          ? `${op.decision.status}: ${op.decision.action}`
                          : "Unknown"}
                      </td>
                      <td>
                        {op.decision?.coverage_complete
                          ? "Complete declared checks"
                          : "Incomplete or unknown"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {operationCursor && (
            <button
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const epoch = generation.current,
                    selected = selection.current;
                  const page = await api(
                    `${base}/${detail.id}/operations?after=${encodeURIComponent(operationCursor)}`,
                  );
                  if (
                    epoch !== generation.current ||
                    selected !== selection.current
                  )
                    return;
                  setOperations((old) => [...old, ...page.items]);
                  setOperationCursor(page.next_after);
                })
              }
            >
              Load more check receipts
            </button>
          )}
        </section>
      )}

      {canApprove && (
        <section aria-label="Register a runtime guardrail policy">
          <h3>Register reviewed rules</h3>
          <p>
            Upload the private registration JSON after authorizing encrypted
            storage with a current capture policy. This creates a pending
            policy; review and approval are separate.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                if (!file || !consent || file.size > 1048576)
                  throw new Error("GUARDRAIL_PRIVATE_FILE_BOUND");
                const epoch = generation.current;
                let body: unknown;
                try {
                  body = JSON.parse(await file.text());
                } catch {
                  throw new Error("GUARDRAIL_REGISTRATION_INVALID");
                }
                if (epoch !== generation.current) return;
                const value = await json(api, base, body);
                if (epoch !== generation.current) return;
                setFile(null);
                setConsent(false);
                if (fileInput.current) fileInput.current.value = "";
                await load();
                await inspect(value.id);
              });
            }}
          >
            <label>
              Private registration JSON
              <input
                ref={fileInput}
                type="file"
                accept=".json,application/json"
                required
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />{" "}
              I authorize this policy content in the configured encrypted store.
            </label>
            <button disabled={busy || !file || !consent}>
              Register pending runtime policy
            </button>
          </form>
        </section>
      )}
    </div>
  );
}
