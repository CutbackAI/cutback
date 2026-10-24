import React, { useRef, useState } from "react";
import {
  API,
  Data,
  Editor,
  Field,
  Form,
  Select,
  Check,
  json,
  money,
  explain,
  Tag,
} from "./ui";

export function Teams({
  api,
  organizations,
  projects,
  bootstrap,
  reload,
}: {
  api: API;
  organizations: Data[];
  projects: Data[];
  bootstrap: boolean;
  reload: () => void;
}) {
  const [organization, setOrganization] = useState(""),
    [members, setMembers] = useState<Data[]>([]),
    [notice, setNotice] = useState("");
  const organizationVersion = useRef(0);
  async function load(id: string) {
    const version = ++organizationVersion.current;
    setOrganization(id);
    setMembers([]);
    setNotice("");
    if (!id) return;
    try {
      const members = await api(`/organizations/${id}/members`);
      if (version === organizationVersion.current) setMembers(members);
    } catch (e) {
      setNotice(explain(e));
    }
  }
  async function act(path: string, body?: unknown) {
    try {
      await api(path, {
        method: body ? "POST" : "DELETE",
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      await load(organization);
    } catch (e) {
      setNotice(explain(e));
    }
  }
  const base = "/organizations/" + organization;
  return (
    <>
      <p>
        Identity claims identify a person. Organization membership and project
        grants determine their access. Membership changes take effect on the
        next request.
      </p>
      {bootstrap && (
        <Editor title="Create organization">
          <Form
            title="New organization"
            onSubmit={async (data) => {
              await json(api, "/organizations", { name: data.name });
              reload();
              return "Organization created.";
            }}
          >
            <Field
              label="Organization name"
              name="name"
              required
              maxLength={200}
            />
          </Form>
        </Editor>
      )}
      <Select
        label="Manage organization"
        name="organization"
        value={organization}
        onChange={(e) => void load(e.target.value)}
      >
        <option value="">Select organization</option>
        {organizations
          .filter((o) =>
            ["owner", "admin", "installation_admin"].includes(o.role),
          )
          .map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
      </Select>
      {organization && (
        <>
          <Editor title="Add identity member">
            <Form
              title="Add member"
              submit="Add member"
              onSubmit={async (data) => {
                await json(api, base + "/members", {
                  issuer: data.issuer,
                  subject: data.subject,
                  role: data.role,
                });
                await load(organization);
                return "Member added. Ordinary members need an explicit project grant.";
              }}
            >
              <Field
                label="Identity issuer URL"
                name="issuer"
                type="url"
                required
              />
              <Field
                label="Identity subject"
                name="subject"
                required
                maxLength={200}
                hint="Exact immutable sub claim from your identity provider. Stored as a hash."
              />
              <Select label="Organization role" name="role">
                <option value="member">Member</option>
                <option value="admin">Administrator</option>
                <option value="owner">Owner</option>
              </Select>
            </Form>
          </Editor>
          <h2>Members and project access</h2>
          {!members.length && (
            <p className="empty">No memberships in this organization.</p>
          )}
          {members.map((member) => (
            <article className="row" key={member.id}>
              <h3>
                {member.role}{" "}
                <Tag>{member.enabled ? "Enabled" : "Disabled"}</Tag>
              </h3>
              <p>
                <code>{member.id}</code>
              </p>
              <p className="hint">
                Issuer {member.issuer} · subject hash{" "}
                <code>{member.subject_hash}</code>
              </p>
              {member.grants.map((grant: Data) => (
                <div className="actions" key={grant.project_id}>
                  <span>
                    {projects.find((p) => p.id === grant.project_id)?.slug ||
                      grant.project_id}{" "}
                    · {grant.role}
                  </span>
                  <button
                    className="secondary"
                    onClick={() =>
                      void act(
                        base +
                          `/members/${member.id}/grants/${grant.project_id}`,
                      )
                    }
                  >
                    Revoke project access
                  </button>
                </div>
              ))}
              {member.enabled && (
                <>
                  <Form
                    title={"Grant project access " + member.id.slice(0, 8)}
                    submit="Save grant"
                    onSubmit={async (data) => {
                      await json(api, base + `/members/${member.id}/grants`, {
                        project_id: data.project,
                        role: data.role,
                      });
                      await load(organization);
                    }}
                  >
                    <Select label="Grant project" name="project" required>
                      {projects
                        .filter((p) => p.organization_id === organization)
                        .map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.slug}
                          </option>
                        ))}
                    </Select>
                    <Select label="Project access role" name="role">
                      {["viewer", "analyst", "editor", "project_admin"].map(
                        (role) => (
                          <option key={role}>{role}</option>
                        ),
                      )}
                    </Select>
                  </Form>
                  <button
                    className="danger"
                    onClick={() => void act(base + `/members/${member.id}`)}
                  >
                    Disable member
                  </button>
                </>
              )}
            </article>
          ))}
        </>
      )}
      {notice && <p role="alert">{notice}</p>}
    </>
  );
}

export function Billing({
  api,
  organizations,
}: {
  api: API;
  organizations: Data[];
}) {
  const [organization, setOrganization] = useState(""),
    [data, setData] = useState<Data | null>(null),
    [notice, setNotice] = useState(""),
    [checkout, setCheckout] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const organizationVersion = useRef(0);
  async function load(id: string) {
    const version = ++organizationVersion.current;
    setOrganization(id);
    setData(null);
    setCheckout("");
    setRequestId(crypto.randomUUID());
    try {
      if (id) {
        const value = await api(`/organizations/${id}/billing`);
        if (version === organizationVersion.current) setData(value);
      }
    } catch (e) {
      setNotice(explain(e));
    }
  }
  const base = "/organizations/" + organization + "/billing";
  return (
    <>
      <p>
        Self-hosted open source does not require a license or subscription. This
        page controls an explicitly configured hosted service.
      </p>
      <Select
        label="Billing organization"
        name="organization"
        value={organization}
        onChange={(e) => void load(e.target.value)}
      >
        <option value="">Select organization</option>
        {organizations
          .filter((o) =>
            ["owner", "admin", "installation_admin"].includes(o.role),
          )
          .map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
      </Select>
      {data && (
        <>
          <div className="metrics">
            <article>
              <h2>Subscription</h2>
              <strong className="metric-word">{data.status}</strong>
              <p>{data.enabled ? "Billing configured" : "Billing disabled"}</p>
            </article>
            <article>
              <h2>Accepted events</h2>
              <strong>{data.accepted_events.toLocaleString()}</strong>
              <p>{data.period}</p>
            </article>
            <article>
              <h2>Modeled hosting subtotal</h2>
              <strong>{money(data.modeled_hosted_subtotal_usd)}</strong>
              <p>Invoice total unknown</p>
            </article>
          </div>
          <Form
            title="Hosted usage caps"
            submit="Save caps"
            onSubmit={async (values) => {
              await json(
                api,
                base + "/caps",
                {
                  monthly_event_cap:
                    values.events === "" ? null : Number(values.events),
                  hosted_spend_cap_usd:
                    values.usd === "" ? null : String(values.usd),
                },
                "PUT",
              );
              await load(organization);
            }}
          >
            <Field
              label="Monthly event cap"
              name="events"
              type="number"
              min="1"
              max="1000000000"
              defaultValue={data.monthly_event_cap ?? ""}
            />
            <Field
              label="Hosted spending cap USD"
              name="usd"
              type="number"
              min="0"
              step="0.01"
              defaultValue={data.hosted_spend_cap_usd ?? ""}
            />
            <p className="hint">
              Caps are enforced only in hosted mode. Provider inference has
              separate experiment reservations.
            </p>
          </Form>
          {data.enabled && (
            <>
              <Form
                title="Subscription checkout"
                submit="Create checkout link"
                description="Review the price on Stripe before accepting. A browser return does not activate a subscription."
                onSubmit={async (values) => {
                  const version = organizationVersion.current;
                  const response = await json(api, base + "/checkout", {
                    price_id: values.price,
                    request_id: requestId,
                  });
                  if (version === organizationVersion.current)
                    setCheckout(response.checkout_url);
                  return "Checkout link created. Open it to review the subscription.";
                }}
              >
                <Select label="Configured hosted price" name="price" required>
                  {data.price_ids.map((id: string) => (
                    <option key={id}>{id}</option>
                  ))}
                </Select>
                <Check
                  label="I authorize creating a checkout session for this organization."
                  name="approved"
                  required
                />
              </Form>
              {checkout && (
                <a
                  className="button-link"
                  href={checkout}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Review subscription in Stripe
                </a>
              )}
              <button
                className="secondary"
                onClick={async () => {
                  try {
                    const version = organizationVersion.current;
                    const row = await json(api, base + "/portal", {});
                    if (version === organizationVersion.current)
                      setCheckout(row.portal_url);
                  } catch (e) {
                    setNotice(explain(e));
                  }
                }}
              >
                Create customer portal link
              </button>
            </>
          )}
        </>
      )}
      {notice && <p role="alert">{notice}</p>}
    </>
  );
}

export function Schedules({
  api,
  base,
  plans,
  items,
  reload,
  admin,
}: {
  api: API;
  base: string;
  plans: Data[];
  items: Data[];
  reload: () => void;
  admin: boolean;
}) {
  const [approval, setApproval] = useState(""),
    [notice, setNotice] = useState("");
  async function act(id: string, body?: unknown) {
    try {
      await api(base + `/schedules/${id}` + (body ? "/approve" : ""), {
        method: body ? "POST" : "DELETE",
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      reload();
    } catch (e) {
      setNotice(explain(e));
    }
  }
  return (
    <>
      <h2>Bounded recurring evaluations</h2>
      <p>
        Every schedule freezes its aggregate run, request and USD caps. The
        customer worker must separately enable scheduling. Missed runs are
        skipped.
      </p>
      <Editor title="Propose evaluation schedule">
        <Form
          title="Schedule an existing frozen plan"
          submit="Freeze schedule"
          onSubmit={async (data) => {
            await json(api, base + "/schedules", {
              plan_id: data.plan,
              interval_seconds: Number(data.hours) * 3600,
              max_runs: Number(data.runs),
              max_total_usd: String(data.usd),
              max_total_requests: Number(data.requests),
              first_run_at: new Date(String(data.start)).toISOString(),
              expires_at: new Date(String(data.end)).toISOString(),
            });
            reload();
            return "Schedule proposed. Exact hash approval is required.";
          }}
        >
          <Select label="Scheduled plan" name="plan" required>
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.specification.workflow} · {p.id.slice(0, 8)}
              </option>
            ))}
          </Select>
          <Field
            label="Interval hours"
            name="hours"
            type="number"
            min="1"
            max="720"
            defaultValue="24"
            required
          />
          <Field
            label="Maximum scheduled runs"
            name="runs"
            type="number"
            min="1"
            max="100"
            defaultValue="1"
            required
          />
          <Field
            label="Total scheduled USD cap"
            name="usd"
            type="number"
            min="0"
            max="100000"
            step="0.01"
            defaultValue="0"
            required
          />
          <Field
            label="Total scheduled request cap"
            name="requests"
            type="number"
            min="1"
            max="1000000"
            defaultValue="100"
            required
          />
          <Field
            label="First scheduled run"
            name="start"
            type="datetime-local"
            required
          />
          <Field
            label="Schedule expiry"
            name="end"
            type="datetime-local"
            required
          />
        </Form>
      </Editor>
      {admin && (
        <Field
          label="Schedule approval reference"
          name="schedule_ref"
          value={approval}
          onChange={(e) => setApproval(e.target.value)}
        />
      )}
      {!items.length && (
        <p className="empty">No recurring evaluations configured.</p>
      )}
      {items.map((row) => (
        <article className="row" key={row.id}>
          <h3>
            {row.enabled ? "Approved" : "Inactive"} · {row.id.slice(0, 8)}
          </h3>
          <p>
            {row.runs_queued} of {row.specification.max_runs} runs queued ·
            reserved {money(row.reserved_usd)} · scheduler{" "}
            {row.scheduler_enabled ? "enabled" : "disabled"}
          </p>
          <p>
            Next eligible {new Date(row.next_run_at).toLocaleString()} · maximum{" "}
            {money(row.specification.max_total_usd)} /{" "}
            {row.specification.max_total_requests} requests
          </p>
          <details>
            <summary>Frozen schedule and approval hash</summary>
            <pre>{JSON.stringify(row, null, 2)}</pre>
          </details>
          {admin && (
            <div className="actions">
              {!row.enabled && (
                <button
                  disabled={!approval}
                  onClick={() =>
                    void act(row.id, {
                      approval_hash: row.approval_hash,
                      approval_ref: approval,
                    })
                  }
                >
                  Approve bounded schedule
                </button>
              )}
              <button className="secondary" onClick={() => void act(row.id)}>
                Disable schedule
              </button>
            </div>
          )}
        </article>
      ))}
      {notice && <p role="alert">{notice}</p>}
    </>
  );
}

export function Rollouts({
  api,
  base,
  items,
  recommendations,
  reload,
  admin,
}: {
  api: API;
  base: string;
  items: Data[];
  recommendations: Data[];
  reload: () => void;
  admin: boolean;
}) {
  const [approval, setApproval] = useState("");
  return (
    <>
      <h2>Canary and rollback records</h2>
      <p>
        These records document operator actions. Cutback does not deploy a
        canary or change production traffic. Only controlled, independent
        production evidence can support a production quality claim.
      </p>
      <Editor title="Propose staging canary">
        <Form
          title="Freeze rollout guardrails"
          submit="Propose rollout"
          onSubmit={async (data) => {
            await json(api, base + "/rollouts", {
              recommendation_id: data.recommendation,
              environment: data.environment,
              baseline_ref: data.baseline,
              rollback_ref: data.rollback,
              max_traffic_percent: Number(data.traffic),
              max_usd: String(data.usd),
            });
            reload();
            return "Rollout proposed with explicit baseline and rollback references.";
          }}
        >
          <Select
            label="Accepted recommendation"
            name="recommendation"
            required
          >
            {recommendations
              .filter((r) => r.status === "accepted_for_staging")
              .map((r) => (
                <option key={r.id} value={r.id}>
                  {r.evidence.candidate?.model || r.id}
                </option>
              ))}
          </Select>
          <Select label="Rollout environment" name="environment">
            <option>staging</option>
            <option>production</option>
          </Select>
          <Field label="Baseline source revision" name="baseline" required />
          <Field label="Rollback source revision" name="rollback" required />
          <Field
            label="Maximum canary traffic percent"
            name="traffic"
            type="number"
            min="0.01"
            max="50"
            step="0.01"
            defaultValue="1"
            required
          />
          <Field
            label="Maximum canary USD"
            name="usd"
            type="number"
            min="0"
            max="10000"
            step="0.01"
            defaultValue="0"
            required
          />
        </Form>
      </Editor>
      {admin && (
        <Field
          label="Rollout approval reference"
          name="rollout_ref"
          value={approval}
          onChange={(e) => setApproval(e.target.value)}
        />
      )}
      {!items.length && <p className="empty">No rollout records.</p>}
      {items.map((row) => (
        <article className="row" key={row.id}>
          <h3>
            {row.status} · {row.id.slice(0, 8)}
          </h3>
          <details>
            <summary>Guardrails, observations and evidence</summary>
            <pre>{JSON.stringify(row, null, 2)}</pre>
          </details>
          {admin && row.status === "PLANNED" && (
            <Form
              title={"Approve rollout " + row.id.slice(0, 8)}
              submit="Approve guardrails"
              onSubmit={async () => {
                await json(api, base + `/rollouts/${row.id}/approve`, {
                  approval_hash: row.approval_hash,
                  approval_ref: approval,
                });
                reload();
              }}
            >
              <Check
                label="I reviewed these frozen guardrails and the rollback revision."
                name="reviewed"
                required
              />
            </Form>
          )}
          {admin && (
            <Form
              title={"Record operator action " + row.id.slice(0, 8)}
              submit="Record action"
              onSubmit={async (data) => {
                await json(api, base + `/rollouts/${row.id}/transition`, {
                  state: data.state,
                  operator_ref: data.reference,
                });
                reload();
              }}
            >
              <Select label="Completed operator action" name="state">
                <option value="DEPLOYED_CANARY">
                  Canary deployed by operator
                </option>
                <option value="ROLLED_BACK">
                  Rollback completed by operator
                </option>
                <option value="REJECTED">Rollout rejected</option>
              </Select>
              <Field
                label="Operator evidence reference"
                name="reference"
                required
              />
            </Form>
          )}
          <Editor title="Record comparison window">
            <Form
              title={"Rollout observations " + row.id.slice(0, 8)}
              submit="Record observations"
              onSubmit={async (data) => {
                const window = (prefix: string) => ({
                  observations: Number(data[prefix + "n"]),
                  error_rate: Number(data[prefix + "errors"]) / 100,
                  schema_failure_rate: Number(data[prefix + "schema"]) / 100,
                  latency_p95_ms:
                    data[prefix + "latency"] === ""
                      ? null
                      : Number(data[prefix + "latency"]),
                  cost_per_request_usd:
                    data[prefix + "cost"] === ""
                      ? null
                      : Number(data[prefix + "cost"]),
                  business_quality:
                    data[prefix + "quality"] === ""
                      ? null
                      : Number(data[prefix + "quality"]) / 100,
                });
                await json(api, base + `/rollouts/${row.id}/observations`, {
                  start: new Date(String(data.start)).toISOString(),
                  end: new Date(String(data.end)).toISOString(),
                  baseline: window("b"),
                  candidate: window("c"),
                  evidence_ref: data.reference,
                  controlled: data.controlled === "on",
                  independent_business_metric: data.independent === "on",
                  synthetic: data.synthetic === "on",
                });
                reload();
              }}
            >
              <Field
                label="Window start"
                name="start"
                type="datetime-local"
                required
              />
              <Field
                label="Window end"
                name="end"
                type="datetime-local"
                required
              />
              <Field
                label="Observation evidence reference"
                name="reference"
                required
              />
              {["b", "c"].map((prefix) => (
                <React.Fragment key={prefix}>
                  <p className="form-section">
                    {prefix === "b" ? "Baseline" : "Candidate"} window
                  </p>
                  {[
                    ["n", "Observations", "1", "100000000", "1"],
                    ["errors", "Error percent", "0", "100", "0.01"],
                    ["schema", "Schema failure percent", "0", "100", "0.01"],
                    ["latency", "p95 latency ms", "0", "", "any"],
                    ["cost", "Cost per request USD", "0", "", "any"],
                    ["quality", "Business quality percent", "0", "100", "0.01"],
                  ].map(([key, label, min, max, step]) => (
                    <Field
                      key={key}
                      label={`${prefix === "b" ? "Baseline" : "Candidate"} ${label}`}
                      name={prefix + key}
                      type="number"
                      min={min}
                      max={max || undefined}
                      step={step}
                      required={["n", "errors", "schema"].includes(key)}
                    />
                  ))}
                </React.Fragment>
              ))}
              <Check label="Controlled comparison" name="controlled" />
              <Check
                label="Business quality was independently measured"
                name="independent"
              />
              <Check
                label="Synthetic observations"
                name="synthetic"
                defaultChecked
              />
            </Form>
          </Editor>
        </article>
      ))}
    </>
  );
}
