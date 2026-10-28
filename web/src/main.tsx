import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import type { components } from "./api.generated";
import {
  ProjectForm,
  PolicyForm,
  KeyForm,
  DatasetForm,
  PlanForm,
  RateForm,
  DataControls,
} from "./forms";
import { Teams, Billing, Schedules, Rollouts } from "./management";
import { explain } from "./ui";
import { Training } from "./training";
import { SSO, handleSSOReturn } from "./sso";
import "./style.css";
import { SampleGuide } from "./demo";
import { PatchReview } from "./change-review";
import { CaseEvidence } from "./case-evidence";
import { AgentWorkflow } from "./agent-workflow";
import { Traces } from "./traces";
import { Imports } from "./imports";
import { Reviews } from "./reviews";
import { Prompts } from "./prompts";
import { Monitoring } from "./monitoring";
import { decisionLabel, criterionLabel, experimentTitle } from "./evidence";

async function request(token: string, path: string, options: RequestInit = {}) {
  const response = await fetch("/v1" + path, {
    ...options,
    headers: {
      Authorization: "Bearer " + token,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
    cache: "no-store",
    redirect: "error",
    credentials: "omit",
    signal: options.signal ?? AbortSignal.timeout(15000),
  });
  const body =
    response.status === 204
      ? null
      : response.headers.get("content-type")?.includes("application/json")
        ? await response.json()
        : response.headers
              .get("content-type")
              ?.includes("application/octet-stream")
          ? await response.blob()
          : await response.text();
  if (!response.ok) throw new Error(body?.code || "REQUEST_FAILED");
  return body;
}

type Inventory = components["schemas"]["Inventory"];
type RecordData = Record<string, any>;
type RequestAPI = (path: string, options?: RequestInit) => Promise<any>;
const views = [
  "Sample",
  "Overview",
  "Agent workflow",
  "Traces",
  "Inventory",
  "Costs",
  "Opportunities",
  "Experiments",
  "Review",
  "Prompts",
  "Monitoring",
  "Changes",
  "Integrations",
  "Security",
  "Training",
  "Teams",
  "Billing",
];
const money = (value: unknown) =>
  value === null || value === undefined
    ? "Unknown"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 6,
      }).format(Number(value));
const count = (value: unknown) =>
  value === null || value === undefined
    ? "Unknown"
    : Number(value).toLocaleString();
function Empty({ children }: { children: React.ReactNode }) {
  return <p className="empty">{children}</p>;
}
function JSONView({ data }: { data: unknown }) {
  return <pre tabIndex={0}>{JSON.stringify(data, null, 2)}</pre>;
}
function Download({ data, name }: { data: unknown; name: string }) {
  const save = () => {
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <button onClick={save}>Export JSON</button>;
}

function App() {
  const [demo, setDemo] = useState<RecordData | null>(null);
  useEffect(() => {
    fetch("/v1/demo", { cache: "no-store", credentials: "omit" })
      .then((r) => (r.ok ? r.json() : null))
      .then((value) => {
        if (value?.available) setDemo(value);
      })
      .catch(() => {});
  }, []);
  const [token, setToken] = useState("");
  const [connected, setConnected] = useState(false);
  const [projects, setProjects] = useState<RecordData[]>([]);
  const [identity, setIdentity] = useState<RecordData>({});
  const [organizations, setOrganizations] = useState<RecordData[]>([]);
  const initial = new URLSearchParams(location.search);
  const [project, setProject] = useState(initial.get("project") || "");
  const [reviewSource, setReviewSource] = useState<{
    project: string;
    id: string;
  } | null>(null);
  const [view, setView] = useState(
    views.includes(initial.get("view") || "")
      ? initial.get("view")!
      : "Overview",
  );
  const [period, setPeriod] = useState(
    ["1h", "7d", "30d", "395d"].includes(initial.get("period") || "")
      ? initial.get("period")!
      : "7d",
  );
  const [groupBy, setGroupBy] = useState(
    [
      "model",
      "provider",
      "workflow",
      "environment",
      "day",
      "call_site",
      "traffic",
    ].includes(initial.get("group") || "")
      ? initial.get("group")!
      : "model",
  );
  const [traffic, setTraffic] = useState(
    ["application", "evaluation", "judge", "unknown"].includes(
      initial.get("traffic") || "",
    )
      ? initial.get("traffic")!
      : "all",
  );
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<RecordData>({});
  const [agentFocus, setAgentFocus] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [updated, setUpdated] = useState("");
  const [refresh, setRefresh] = useState(false);
  const requestVersion = useRef(0);
  const sessionVersion = useRef(0);
  const [projectOffset, setProjectOffset] = useState(0);
  const [projectNext, setProjectNext] = useState(false);
  const api: RequestAPI = useCallback(
    (path, options = {}) => request(token, path, options),
    [token],
  );
  useEffect(() => {
    const params = new URLSearchParams({
      view,
      period,
      group: groupBy,
      traffic,
    });
    if (project) params.set("project", project);
    history.replaceState(null, "", "?" + params);
    setOffset(0);
  }, [view, project, period, groupBy, traffic]);
  const load = useCallback(async () => {
    if (!connected || !project || ["Teams", "Billing"].includes(view)) return;
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    const base = "/projects/" + encodeURIComponent(project);
    try {
      let result: RecordData = {};
      const from = new Date(
        Date.now() -
          Number(period.slice(0, -1)) *
            (period.endsWith("h") ? 3600000 : 86400000),
      ).toISOString();
      const usage =
        "/usage?start=" +
        encodeURIComponent(from) +
        "&group_by=" +
        encodeURIComponent(groupBy) +
        (traffic === "all" ? "" : "&traffic_kind=" + traffic);
      if (view === "Overview") {
        const [report, health] = await Promise.all([
          api(base + usage),
          api(base + "/health"),
        ]);
        result = { report, health };
      }
      if (view === "Agent workflow") {
        const [source, plans, runs] = await Promise.all([
          api(base + "/inventory"),
          api(base + "/experiments/plans"),
          api(base + "/experiments"),
        ]);
        result = { inventory: source.inventory, plans, runs };
      }
      if (view === "Inventory")
        result = await api(
          base + "/inventory?start=" + encodeURIComponent(from),
        );
      if (view === "Costs") result = { report: await api(base + usage) };
      if (view === "Opportunities")
        result = await api(
          base + "/opportunities?start=" + encodeURIComponent(from),
        );
      if (view === "Experiments") {
        const [datasets, runs, plans] = await Promise.all([
          api(base + "/datasets?offset=" + offset),
          api(base + "/experiments?offset=" + offset),
          api(base + "/experiments/plans?offset=" + offset),
        ]);
        result = {
          datasets,
          runs,
          plans,
          schedules: await api(base + "/schedules"),
          imports: await api(base + "/imports"),
          policies: ["admin", "project_admin"].includes(
            identity.project_roles?.[project] || identity.role,
          )
            ? await api(base + "/capture-policies")
            : [],
        };
      }
      if (view === "Training") {
        const [jobs, config, datasets] = await Promise.all([
          api(base + "/training"),
          api(base + "/training/config"),
          api(base + "/datasets"),
        ]);
        result = { jobs, config, datasets };
      }
      if (view === "Review") {
        const isAdmin = ["admin", "project_admin"].includes(
          identity.project_roles?.[project] || identity.role,
        );
        result = {
          queues: await api(base + "/reviews"),
          datasets: await api(base + "/datasets"),
          policies: isAdmin ? await api(base + "/capture-policies") : [],
          principals: isAdmin
            ? await api(base + "/reviews/principals")
            : { items: [] },
        };
      }
      if (view === "Prompts") {
        const isAdmin = ["admin", "project_admin"].includes(
          identity.project_roles?.[project] || identity.role,
        );
        result = {
          prompts: await api(base + "/prompts"),
          datasets: await api(base + "/datasets"),
          policies: isAdmin ? await api(base + "/capture-policies") : [],
          grants: isAdmin
            ? await api(base + "/prompt-read-grants")
            : { items: [] },
        };
      }
      if (view === "Monitoring")
        result = {
          online: await api(base + "/online-rules"),
          alerts: await api(base + "/alert-rules"),
          incidents: await api(base + "/incidents"),
          savedViews: await api(base + "/saved-views"),
          notifications: await api(base + "/notifications"),
          deliveries: await api(base + "/notification-deliveries"),
          destinations: ["admin", "project_admin"].includes(
            identity.project_roles?.[project] || identity.role,
          )
            ? await api(base + "/notification-destinations")
            : { identifiers: [] },
        };
      if (view === "Changes")
        result = {
          recommendations: await api(base + "/recommendations"),
          patches: await api(base + "/changes"),
          rollouts: await api(base + "/rollouts"),
        };
      if (view === "Integrations")
        result = { health: await api(base + "/health") };
      if (view === "Security") {
        const [policies, prices, audit, keys] = await Promise.all([
          api(base + "/capture-policies"),
          api(base + "/prices"),
          api(base + "/audit?offset=" + offset),
          api(base + "/keys"),
        ]);
        result = {
          policies,
          prices,
          audit,
          keys,
          settings: await api(base + "/settings"),
          cache: await api(base + "/cache"),
        };
      }
      if (version === requestVersion.current) {
        setData(result);
        setUpdated(new Date().toLocaleTimeString());
      }
    } catch (e) {
      if (version === requestVersion.current) {
        setError(explain(e));
        setData({});
      }
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, [
    connected,
    project,
    view,
    period,
    groupBy,
    traffic,
    offset,
    api,
    identity,
  ]);
  useEffect(() => {
    setData({});
    load();
  }, [load]);
  useEffect(() => {
    if (!refresh) return;
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [refresh, load]);
  async function refreshProjects(selected?: string) {
    const version = sessionVersion.current;
    const rows = await api("/projects?limit=100&offset=" + projectOffset);
    const result = [...rows];
    if (selected && !result.some((p: RecordData) => p.id === selected))
      result.unshift(await api("/projects/" + encodeURIComponent(selected)));
    const orgs =
      identity.bootstrap || identity.role === "oidc"
        ? await api("/organizations")
        : [];
    if (version !== sessionVersion.current) return;
    setData({});
    setProjects(result);
    setProjectNext(rows.length === 100);
    setProject(
      selected && result.some((p: RecordData) => p.id === selected)
        ? selected
        : result.some((p: RecordData) => p.id === project)
          ? project
          : result[0]?.id || "",
    );
    setOrganizations(orgs);
  }
  async function projectPage(direction: number) {
    const version = sessionVersion.current;
    const next = Math.max(0, projectOffset + direction * 100);
    try {
      const rows = await api("/projects?limit=100&offset=" + next);
      if (version !== sessionVersion.current) return;
      requestVersion.current++;
      setData({});
      setProjects(rows);
      setProjectOffset(next);
      setProjectNext(rows.length === 100);
      setProject(rows[0]?.id || "");
    } catch (e) {
      if (version === sessionVersion.current) setError(explain(e));
    }
  }
  async function connect(credential: string) {
    const version = ++sessionVersion.current;
    setError("");
    try {
      const [result, me] = await Promise.all([
        request(credential, "/projects?limit=100"),
        request(credential, "/me"),
      ]);
      const orgs =
        me.bootstrap || me.role === "oidc"
          ? await request(credential, "/organizations")
          : [];
      if (project && !result.some((p: RecordData) => p.id === project)) {
        try {
          result.unshift(
            await request(
              credential,
              "/projects/" + encodeURIComponent(project),
            ),
          );
        } catch {}
      }
      if (version !== sessionVersion.current) return;
      setProjectOffset(0);
      setProjectNext(result.length >= 100);
      setData({});
      setToken(credential);
      setIdentity(me);
      setProjects(result);
      setOrganizations(orgs);
      setConnected(true);
      setView("Overview");
      if (!result.some((p: RecordData) => p.id === project))
        setProject(result[0]?.id || "");
    } catch (error) {
      if (version === sessionVersion.current) throw error;
    }
  }
  async function login(event: React.FormEvent) {
    event.preventDefault();
    try {
      await connect(token);
    } catch (e) {
      setError(explain(e));
    }
  }
  function logout() {
    sessionVersion.current++;
    requestVersion.current++;
    setConnected(false);
    setToken("");
    setData({});
    setProjects([]);
    setOrganizations([]);
    setIdentity({});
    setError("");
  }
  const projectRole = identity.project_roles?.[project] || identity.role;
  const admin = ["admin", "project_admin"].includes(projectRole);
  const analyst = ["admin", "project_admin", "editor", "analyst"].includes(
    projectRole,
  );
  const canCreate =
    identity.bootstrap ||
    Object.values(identity.organizations || {}).some((role) =>
      ["owner", "admin"].includes(String(role)),
    );
  const availableViews = views.filter((name) =>
    name === "Sample"
      ? !!demo
      : name === "Security"
        ? admin
        : name === "Training"
          ? analyst
          : ["Teams", "Billing"].includes(name)
            ? identity.bootstrap ||
              Object.values(identity.organizations || {}).some((role) =>
                ["owner", "admin"].includes(String(role)),
              )
            : true,
  );
  const base = "/projects/" + encodeURIComponent(project);
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header>
        <a className="brand" href="/dashboard/">
          cutback<span> / evidence before change</span>
        </a>
        {connected && <button onClick={logout}>Disconnect</button>}
      </header>
      {!connected ? (
        <main id="main" className="login">
          <p className="eyebrow">CUSTOMER OWNED · OPEN SOURCE</p>
          <h1>
            Understand your AI spend.
            <br />
            Keep the evidence.
          </h1>
          <p>
            Inspect observed usage, test alternatives, and review changes.
            Source scans alone cannot show historical spend.
          </p>
          {demo && (
            <section className="demo-start">
              <h2>Try the support-desk sample</h2>
              <p>
                Explore a working application, compare a safe and unsafe
                candidate, and inspect the decision. Every provider reply is
                synthetic.
              </p>
              <button
                onClick={async () => {
                  try {
                    const response = await fetch("/v1/demo/connect", {
                      method: "POST",
                      headers: {
                        "Content-Type": "application/json",
                        "X-Cutback-Demo": "connect",
                      },
                      body: "{}",
                      credentials: "omit",
                    });
                    if (!response.ok)
                      throw new Error("DEMO_CONNECTION_REFUSED");
                    const value = await response.json();
                    await connect(value.token);
                    setView("Sample");
                  } catch (e) {
                    setError(explain(e));
                  }
                }}
              >
                Explore sample
              </button>
            </section>
          )}
          <SSO connected={connect} />
          <form onSubmit={login}>
            <label htmlFor="token">Control API key</label>
            <input
              id="token"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              required
            />
            <p className="hint">
              Held in memory for this session. Ingestion keys cannot read the
              dashboard.
            </p>
            <button type="submit">Connect to this collector</button>
          </form>
          {error && <p role="alert">{error}</p>}
        </main>
      ) : (
        <div className="shell">
          <aside>
            <label htmlFor="project">Project</label>
            <select
              id="project"
              value={project}
              onChange={(e) => {
                requestVersion.current++;
                setData({});
                setProject(e.target.value);
                const role =
                  identity.project_roles?.[e.target.value] || identity.role;
                if (
                  view === "Security" &&
                  !["admin", "project_admin"].includes(role)
                )
                  setView("Overview");
              }}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {projects.some(
                    (other) => other.id !== p.id && other.slug === p.slug,
                  )
                    ? (organizations.find((org) => org.id === p.organization_id)
                        ?.name || p.organization_id.slice(0, 8)) +
                      " / " +
                      p.slug
                    : p.slug}
                </option>
              ))}
            </select>
            {(projectOffset > 0 || projectNext) && (
              <div className="project-pages">
                <button
                  className="secondary"
                  aria-label="Previous projects"
                  disabled={!projectOffset}
                  onClick={() => void projectPage(-1)}
                >
                  ←
                </button>
                <span>
                  Projects {projectOffset + 1}–
                  {projectOffset + Math.min(projects.length, 100)}
                </span>
                <button
                  className="secondary"
                  aria-label="Next projects"
                  disabled={!projectNext}
                  onClick={() => void projectPage(1)}
                >
                  →
                </button>
              </div>
            )}
            <nav aria-label="Dashboard">
              {availableViews.map((name) => (
                <button
                  key={name}
                  aria-current={view === name ? "page" : undefined}
                  onClick={() => {
                    requestVersion.current++;
                    setData({});
                    setView(name);
                  }}
                >
                  {name}
                </button>
              ))}
            </nav>
            <p className="hint">
              Metadata by default.
              <br />
              No automatic deployments.
            </p>
          </aside>
          <main id="main">
            {demo && (
              <p className="disclosure">
                Synthetic sample workspace · no provider charges · fixture
                results are not customer savings.
              </p>
            )}
            <div className="page-title">
              <div>
                <p className="eyebrow">PROJECT EVIDENCE</p>
                <h1>{view}</h1>
              </div>
              <button onClick={load} disabled={loading}>
                Refresh
              </button>
            </div>
            <div className="toolbar">
              {["Overview", "Inventory", "Costs", "Opportunities"].includes(
                view,
              ) && (
                <label>
                  Period
                  <select
                    value={period}
                    onChange={(e) => setPeriod(e.target.value)}
                  >
                    {["1h", "7d", "30d", "395d"].map((p) => (
                      <option key={p}>{p}</option>
                    ))}
                  </select>
                </label>
              )}
              {view === "Costs" && (
                <label>
                  Group by
                  <select
                    value={groupBy}
                    onChange={(e) => setGroupBy(e.target.value)}
                  >
                    {[
                      "model",
                      "provider",
                      "workflow",
                      "environment",
                      "day",
                      "call_site",
                      "traffic",
                    ].map((g) => (
                      <option key={g}>{g}</option>
                    ))}
                  </select>
                </label>
              )}
              {["Overview", "Costs"].includes(view) && (
                <label>
                  Traffic purpose
                  <select
                    value={traffic}
                    onChange={(e) => setTraffic(e.target.value)}
                  >
                    <option value="all">All reported purposes</option>
                    <option value="application">Application calls</option>
                    <option value="evaluation">Evaluation calls</option>
                    <option value="judge">Judge calls</option>
                    <option value="unknown">Unknown / legacy</option>
                  </select>
                </label>
              )}
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={refresh}
                  onChange={(e) => setRefresh(e.target.checked)}
                />
                Refresh every 30 seconds
              </label>
              <span role="status">
                {loading ? "Loading…" : updated ? "Updated " + updated : ""}
              </span>
            </div>
            {error && (
              <p role="alert" className="notice">
                {error}. Check the key’s role and project scope.
              </p>
            )}
            {!project && (
              <Empty>
                No projects yet. Create an isolated project to connect your
                first application.
              </Empty>
            )}
            {canCreate && (
              <details className="editor">
                <summary>Create a project</summary>
                <ProjectForm
                  api={api}
                  organizations={organizations.filter((o) =>
                    ["owner", "admin", "installation_admin"].includes(o.role),
                  )}
                  created={refreshProjects}
                />
              </details>
            )}
            {view === "Traces" && project && (
              <Traces
                key={project}
                api={api}
                project={project}
                allowContent={admin}
                onReview={(id) => {
                  setReviewSource({ project, id });
                  setView("Review");
                }}
              />
            )}
            {view === "Training" && data.jobs && (
              <Training
                key={project}
                api={api}
                base={base}
                data={data}
                reload={load}
                admin={admin}
              />
            )}
            {view === "Sample" && demo && (
              <SampleGuide
                demo={demo}
                api={api}
                navigate={setView}
                evidence={(detail) => (
                  <>
                    <RunEvidence detail={detail} />
                    {detail.results?.case_outputs_retained && admin && (
                      <CaseEvidence
                        key={detail.id}
                        api={api}
                        base={base}
                        runId={detail.id}
                        models={detail.results?.models?.map(
                          (row: RecordData) => row.model.key,
                        )}
                        strata={Array.from(
                          new Set<string>(
                            detail.results?.models?.flatMap(
                              (row: RecordData) =>
                                row.slices?.map(
                                  (slice: RecordData) => slice.stratum,
                                ) || [],
                            ) || [],
                          ),
                        )}
                      />
                    )}
                  </>
                )}
              />
            )}
            {view === "Teams" && (
              <Teams
                api={api}
                organizations={organizations}
                projects={projects}
                bootstrap={!!identity.bootstrap}
                reload={() => void refreshProjects()}
              />
            )}
            {view === "Billing" && (
              <Billing api={api} organizations={organizations} />
            )}
            {view === "Overview" && data.report && (
              <Overview
                report={data.report}
                health={data.health}
                openAgent={() => setView("Agent workflow")}
              />
            )}
            {view === "Agent workflow" && data.plans && (
              <AgentWorkflow
                inventory={data.inventory}
                plans={data.plans}
                runs={data.runs}
                project={project}
                focus={agentFocus}
              />
            )}
            {view === "Costs" && data.report && <Costs report={data.report} />}
            {view === "Inventory" && "inventory" in data && (
              <InventoryView
                inventory={data.inventory}
                runtime={data.runtime_by_call_site || {}}
                truncated={data.runtime_groups_truncated}
              />
            )}
            {view === "Opportunities" && data.opportunities && (
              <Opportunities
                items={data.opportunities}
                overhead={data.overhead}
                prepare={(workflow) => {
                  setAgentFocus(workflow);
                  setView("Agent workflow");
                }}
              />
            )}
            {view === "Experiments" && data.datasets && (
              <Experiments
                key={project}
                data={data}
                api={api}
                base={base}
                reload={load}
                admin={admin}
                analyst={analyst}
              />
            )}
            {view === "Review" && data.queues && (
              <Reviews
                key={project}
                api={api}
                base={base}
                data={data}
                admin={admin}
                analyst={analyst}
                reload={load}
                traceSource={
                  reviewSource?.project === project
                    ? reviewSource.id
                    : undefined
                }
              />
            )}
            {view === "Prompts" && data.prompts && (
              <Prompts
                key={project}
                api={api}
                base={base}
                data={data}
                admin={admin}
                analyst={analyst}
                editor={["admin", "project_admin", "editor"].includes(
                  projectRole,
                )}
              />
            )}
            {view === "Monitoring" && data.online && (
              <Monitoring
                key={project}
                api={api}
                base={base}
                data={data}
                admin={admin}
                editor={["admin", "project_admin", "editor"].includes(
                  projectRole,
                )}
                reload={load}
                onReview={(id) => {
                  setReviewSource({ project, id });
                  setView("Review");
                }}
              />
            )}
            {view === "Changes" && data.recommendations && (
              <Changes
                key={project}
                items={data.recommendations}
                patches={data.patches || []}
                rollouts={data.rollouts}
                api={api}
                base={base}
                reload={load}
                admin={admin}
                analyst={analyst}
              />
            )}
            {view === "Integrations" && data.health && (
              <Integrations health={data.health} />
            )}
            {view === "Security" && data.policies && (
              <Security
                key={project}
                data={data}
                api={api}
                base={base}
                reload={load}
                slug={projects.find((p) => p.id === project)?.slug || ""}
                deleted={() => refreshProjects()}
              />
            )}
            {["Experiments", "Security"].includes(view) && (
              <div className="pager">
                <button
                  disabled={!offset}
                  onClick={() => setOffset(Math.max(0, offset - 50))}
                >
                  Previous page
                </button>
                <span>Offset {offset}</span>
                <button
                  disabled={
                    view === "Experiments"
                      ? Math.max(
                          data.datasets?.length || 0,
                          data.runs?.length || 0,
                          data.plans?.length || 0,
                        ) < 50
                      : (data.audit?.length || 0) < 50
                  }
                  onClick={() => setOffset(offset + 50)}
                >
                  Next page
                </button>
              </div>
            )}
          </main>
        </div>
      )}
      <footer>
        Cutback · modeled prices are estimates, never verified provider
        invoices.
      </footer>
    </>
  );
}

function Overview({
  report,
  health,
  openAgent,
}: {
  report: RecordData;
  health: RecordData;
  openAgent: () => void;
}) {
  const s = report.summary;
  return (
    <>
      <p>
        Start in your application. Your coding agent can prepare discovery,
        instrumentation and a bounded evaluation, then bring the evidence here.
      </p>
      <button onClick={openAgent}>Start the agent workflow</button>
      <p>
        Observed period: <time>{report.period.from}</time> to{" "}
        <time>{report.period.to}</time>.
      </p>
      <div className="metrics">
        <article>
          <h2>Modeled cost</h2>
          <strong>{money(s.modeled_cost_usd)}</strong>
          <p>Known subtotal {money(s.known_modeled_cost_subtotal_usd)}</p>
        </article>
        <article>
          <h2>Observed attempts</h2>
          <strong>{count(s.observed_attempts)}</strong>
          <p>{count(s.logical_operations)} linked operations</p>
        </article>
        <article>
          <h2>Unpriced attempts</h2>
          <strong>{count(s.unpriced_requests)}</strong>
          <p>Population coverage: unknown</p>
        </article>
        <article>
          <h2>Latency p95</h2>
          <strong>
            {s.latency_p95_ms === null
              ? "Unknown"
              : Math.round(s.latency_p95_ms) + " ms"}
          </strong>
          <p>Quality: not verified</p>
        </article>
      </div>
      {!s.observed_attempts && (
        <Empty>
          No observations in this period. Scan locally, enable a supported
          adapter, and send authorized traffic to this collector.
        </Empty>
      )}
      <h2>Evidence and gaps</h2>
      <p className="notice">
        No savings are production verified here. Cost estimates cover recorded
        usage and supported rates. Lost or uninstrumented calls are not known.
      </p>
      <p>
        Pricing snapshots:{" "}
        {s.price_snapshot_ids?.join(", ") || "None in this period"}
      </p>
      <Download data={report} name="cutback-usage.json" />
      <Integrations health={health} />
    </>
  );
}
function InventoryView({
  inventory,
  runtime,
  truncated,
}: {
  inventory: Inventory | null;
  runtime: RecordData;
  truncated?: boolean;
}) {
  const [query, setQuery] = useState(""),
    [provider, setProvider] = useState("all");
  const sites =
    inventory?.call_sites.filter(
      (site) =>
        (provider === "all" || site.provider === provider) &&
        [
          site.source_file,
          site.source_symbol,
          site.requested_model,
          site.sdk_surface,
        ].some((value) =>
          String(value || "")
            .toLowerCase()
            .includes(query.toLowerCase()),
        ),
    ) || [];
  if (!inventory)
    return (
      <Empty>
        No scan uploaded. Run cutback scan, then cutback scans upload.
      </Empty>
    );
  return (
    <>
      <p>
        Static source sites joined to reported application calls in this period.
        Evaluation traffic is excluded. No observations means unknown usage, not
        zero usage.
      </p>
      <div className="toolbar">
        <label>
          Find call site
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="File, symbol or model"
          />
        </label>
        <label>
          Provider filter
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="all">All providers</option>
            {Array.from(
              new Set(inventory.call_sites.map((site) => site.provider)),
            ).map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <span>
          {sites.length} of {inventory.call_sites.length} call sites
        </span>
      </div>
      <div className="table-scroll" tabIndex={0}>
        <table>
          <caption>Discovered candidate call sites</caption>
          <thead>
            <tr>
              {[
                "Source",
                "Provider / surface",
                "Model",
                "Confidence",
                "Application observations",
                "Known modeled cost",
                "Missing evidence",
              ].map((v) => (
                <th key={v}>{v}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sites.map((s: any) => (
              <tr key={s.call_site_id}>
                <td>
                  <code>
                    {s.source_file}:{s.line}
                  </code>
                  <br />
                  {s.source_symbol}
                  <br />
                  <span className="hint">
                    Workflows:{" "}
                    {s.entry_points
                      ?.map((entry: RecordData) =>
                        entry.symbol.split(".").at(-1),
                      )
                      .join(", ") || "Unknown"}
                  </span>
                </td>
                <td>
                  {s.provider}
                  <br />
                  {s.sdk_surface}
                </td>
                <td>{s.requested_model || "Runtime unresolved"}</td>
                <td>{s.confidence}</td>
                <td>
                  {runtime[s.call_site_id]
                    ? count(runtime[s.call_site_id].observed_attempts)
                    : "Unknown"}
                </td>
                <td>
                  {money(
                    runtime[s.call_site_id]?.known_modeled_cost_subtotal_usd,
                  )}
                </td>
                <td>
                  {s.unresolved.join(", ") ||
                    (runtime[s.call_site_id]
                      ? "Reported source identity; population coverage unknown"
                      : "Runtime observation needed")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncated && (
        <p className="notice">
          Runtime groups are bounded. Narrow the period to inspect omitted
          sites.
        </p>
      )}
      <h2>Scanner coverage and warnings</h2>
      <JSONView
        data={{ coverage: inventory.coverage, warnings: inventory.warnings }}
      />
      <Download data={inventory} name="cutback-inventory.json" />
    </>
  );
}
function Costs({ report }: { report: RecordData }) {
  const max = Math.max(
    0,
    ...report.groups.map((g: RecordData) =>
      Number(g.known_modeled_cost_subtotal_usd || 0),
    ),
  );
  return (
    <>
      {!!report.summary.external_otel_observations && (
        <p className="notice">
          {count(report.summary.external_otel_observations)} external OTel spans
          are included. A span may combine SDK retries; it does not establish
          complete request or billing coverage. Export each call through one
          observation path.
        </p>
      )}
      <p>
        {report.period.from} → {report.period.to}. Rates:{" "}
        {report.summary.price_snapshot_ids.join(", ") || "No priced usage"}.
      </p>
      <p className="notice">
        {count(report.summary.unpriced_requests)} attempts have unknown cost.
        Known subtotal: {money(report.summary.known_modeled_cost_subtotal_usd)}.
        Invoice total: unknown.
      </p>
      {!report.groups.length ? (
        <Empty>No observations in this period.</Empty>
      ) : (
        <>
          <svg
            className="chart"
            viewBox={`0 0 800 ${report.groups.slice(0, 10).length * 40 + 25}`}
            role="img"
            aria-label="Known modeled cost subtotals by group; exact values in the table below"
          >
            {report.groups.slice(0, 10).map((g: RecordData, i: number) => (
              <g key={g.key}>
                <text x="0" y={i * 40 + 22}>
                  {(g.label || g.key).slice(0, 32)}
                </text>
                <rect
                  x="240"
                  y={i * 40 + 8}
                  width={
                    max
                      ? (Number(g.known_modeled_cost_subtotal_usd || 0) / max) *
                        480
                      : 0
                  }
                  height="22"
                />
              </g>
            ))}
          </svg>
          <div className="table-scroll" tabIndex={0}>
            <table>
              <caption>Observed usage by {report.group_by}</caption>
              <thead>
                <tr>
                  {[
                    "Group",
                    "Attempts",
                    "Modeled cost USD",
                    "Known subtotal USD",
                    "Unpriced",
                    "Input tokens",
                    "Output tokens",
                    "p95 ms",
                  ].map((t) => (
                    <th key={t}>{t}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.groups.map((g: RecordData) => (
                  <tr key={g.key}>
                    <th scope="row">{g.label || g.key}</th>
                    <td>{count(g.observed_attempts)}</td>
                    <td>{money(g.modeled_cost_usd)}</td>
                    <td>{money(g.known_modeled_cost_subtotal_usd)}</td>
                    <td>{count(g.unpriced_requests)}</td>
                    <td>{count(g.input_tokens_observed)}</td>
                    <td>{count(g.output_tokens_observed)}</td>
                    <td>{count(g.latency_p95_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {report.groups_truncated && (
        <p>Top 100 groups shown; narrow the period for more detail.</p>
      )}
      {report.retention?.partial_days_excluded?.length > 0 && (
        <p role="alert">
          Archived partial days are excluded; totals are incomplete.
        </p>
      )}
      <Download data={report} name="cutback-costs.json" />
    </>
  );
}
function Opportunities({
  items,
  overhead,
  prepare,
}: {
  items: RecordData[];
  overhead?: RecordData;
  prepare: (workflow: string) => void;
}) {
  const [tier, setTier] = useState("all");
  const filtered = items.filter(
    (row) => tier === "all" || String(row.evidence_tier) === tier,
  );
  return (
    <>
      <p>
        Ranked from application traffic. Evaluation and judge traffic are
        accounted for separately.
      </p>
      {overhead && (
        <details>
          <summary>Evaluation overhead and unknown traffic</summary>
          <JSONView data={overhead} />
        </details>
      )}
      <label>
        Evidence tier filter
        <select value={tier} onChange={(e) => setTier(e.target.value)}>
          <option value="all">All evidence tiers</option>
          {[1, 2, 3, 4, 5].map((value) => (
            <option key={value} value={value}>
              Tier {value}
            </option>
          ))}
        </select>
      </label>
      {!items.length ? (
        <Empty>
          No observed opportunities yet. Record supported traffic and check
          pricing coverage.
        </Empty>
      ) : (
        filtered.map((item, i) => (
          <article className="row" key={i}>
            <h2>{item.strategy.replaceAll("_", " ")}</h2>
            <p>
              {item.label || item.target} · tier {item.evidence_tier} · quality{" "}
              {item.quality_status}
            </p>
            <p>{item.reason.replaceAll("_", " ")}</p>
            <p>
              Expected savings: unknown. Next:{" "}
              {item.next_action.replaceAll("_", " ")}.
            </p>
            <details>
              <summary>View source evidence</summary>
              <JSONView data={item.evidence} />
            </details>
            <button onClick={() => prepare(item.target)}>
              Prepare with agent
            </button>
          </article>
        ))
      )}
    </>
  );
}
function Mutation({
  title,
  endpoint,
  api,
  reload,
  description,
}: {
  title: string;
  endpoint: string;
  api: RequestAPI;
  reload: () => void;
  description: string;
}) {
  const [body, setBody] = useState("");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const parsed = JSON.parse(body);
      setResult(
        await api(endpoint, { method: "POST", body: JSON.stringify(parsed) }),
      );
      reload();
    } catch (e) {
      setResult({ code: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>{title}</summary>
      <p>{description}</p>
      <form onSubmit={submit}>
        <label>
          {title} JSON
          <textarea
            rows={6}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            required
            spellCheck={false}
          />
        </label>
        <button disabled={busy}>{busy ? "Submitting…" : title}</button>
      </form>
      {result && <JSONView data={result} />}
    </details>
  );
}
function Experiments({
  data,
  api,
  base,
  reload,
  admin,
  analyst,
}: {
  data: RecordData;
  api: RequestAPI;
  base: string;
  reload: () => void;
  admin: boolean;
  analyst: boolean;
}) {
  const [detail, setDetail] = useState<any>(null);
  const [notice, setNotice] = useState("");
  const [approval, setApproval] = useState("");
  async function act(fn: () => Promise<any>) {
    try {
      setNotice("");
      setDetail(await fn());
      reload();
    } catch (e) {
      setNotice((e as Error).message);
    }
  }
  return (
    <>
      <p className="notice">
        Datasets require rights approval and encrypted storage. Plans make no
        provider calls. Each approved budget permits one queued run; execution
        is disabled by default.
      </p>
      <Imports
        api={api}
        base={base}
        policies={data.policies || []}
        rows={data.imports}
        analyst={analyst}
        reload={reload}
      />
      <h2>Frozen datasets</h2>
      {!data.datasets.length ? (
        <Empty>No authorized datasets imported.</Empty>
      ) : (
        data.datasets.map((d: RecordData) => (
          <details key={d.id}>
            <summary>
              {d.workflow} · {d.id}
            </summary>
            <JSONView data={d} />
          </details>
        ))
      )}
      {analyst && (
        <DatasetForm
          api={api}
          base={base}
          reload={reload}
          policies={data.policies || []}
        />
      )}
      {analyst && (
        <PlanForm
          api={api}
          base={base}
          reload={reload}
          datasets={data.datasets}
        />
      )}
      <h2>Experiment plans</h2>
      <label htmlFor="approval">
        Approval reference (required to approve a budget)
      </label>
      <input
        id="approval"
        value={approval}
        onChange={(e) => setApproval(e.target.value)}
      />
      {!data.plans.length ? (
        <Empty>
          No frozen plans. Generate a plan with the CLI or submit the versioned
          API contract below.
        </Empty>
      ) : (
        data.plans.map((p: RecordData) => (
          <article className="row" key={p.id}>
            <h3>
              {experimentTitle(p.specification)} · {p.specification.stage} ·{" "}
              {p.approval_status}
            </h3>
            <p>
              Maximum budget {money(p.specification.budget.max_usd)} ·{" "}
              {p.specification.budget.max_requests} requests · hash{" "}
              <code>{p.plan_hash}</code>
            </p>
            <details>
              <summary>Frozen specification</summary>
              <JSONView data={p} />
            </details>
            {admin && p.approval_status === "pending" && (
              <button
                disabled={!approval}
                onClick={() =>
                  act(() =>
                    api(base + `/experiments/plans/${p.id}/approve`, {
                      method: "POST",
                      body: JSON.stringify({
                        plan_hash: p.plan_hash,
                        approval_ref: approval,
                      }),
                    }),
                  )
                }
              >
                Approve this budget
              </button>
            )}
            {analyst && p.approval_status === "approved" && (
              <button
                onClick={() =>
                  act(() =>
                    api(base + "/experiments", {
                      method: "POST",
                      body: JSON.stringify({ plan_id: p.id }),
                    }),
                  )
                }
              >
                Queue approved run
              </button>
            )}
          </article>
        ))
      )}
      {analyst && (
        <Mutation
          title="Create advanced experiment plan"
          endpoint={base + "/experiments/plans"}
          api={api}
          reload={reload}
          description="Submit dataset_id and specification. Plans freeze models, evaluator, dataset hash, provider allowlist and budget; they do not execute."
        />
      )}
      <h2>Runs</h2>
      {!data.runs.length ? (
        <Empty>No experiment runs.</Empty>
      ) : (
        data.runs.map((r: RecordData) => (
          <article className="row" key={r.id}>
            <h3>
              {experimentTitle(r.specification)} ·{" "}
              {r.specification?.stage || "full"} · {r.status}
            </h3>
            <p>
              Reserved {money(r.reserved_usd)} · known spend subtotal{" "}
              {money(r.spent_usd)} · {r.requests} requests
            </p>
            <button
              onClick={() => act(() => api(base + "/experiments/" + r.id))}
            >
              View reproducible evidence
            </button>
            {r.status === "BLOCKED" && (
              <p role="status">
                Waiting for configuration:{" "}
                {r.block_reason || "Review worker provider configuration"}. No
                provider request has started.
              </p>
            )}
            {analyst && r.status === "BLOCKED" && (
              <button
                onClick={() =>
                  act(() =>
                    api(base + `/experiments/${r.id}/recheck`, {
                      method: "POST",
                      body: "{}",
                    }),
                  )
                }
              >
                Recheck configuration
              </button>
            )}
            {analyst && ["QUEUED", "BLOCKED", "RUNNING"].includes(r.status) && (
              <button
                onClick={() =>
                  act(() =>
                    api(base + `/experiments/${r.id}/cancel`, {
                      method: "POST",
                      body: "{}",
                    }),
                  )
                }
              >
                Cancel further calls
              </button>
            )}
          </article>
        ))
      )}
      {analyst && (
        <Schedules
          api={api}
          base={base}
          plans={data.plans}
          items={data.schedules || []}
          reload={reload}
          admin={admin}
        />
      )}
      {notice && <p role="alert">{notice}</p>}
      {detail && (
        <>
          <h2>Experiment decision</h2>
          <RunEvidence detail={detail} />
          {detail.results?.case_outputs_retained && admin && (
            <CaseEvidence
              key={`${base}:${detail.id}`}
              api={api}
              base={base}
              runId={detail.id}
              models={detail.results?.models?.map(
                (row: RecordData) => row.model.key,
              )}
              strata={Array.from(
                new Set<string>(
                  detail.results?.models?.flatMap(
                    (row: RecordData) =>
                      row.slices?.map((slice: RecordData) => slice.stratum) ||
                      [],
                  ) || [],
                ),
              )}
            />
          )}
          <details>
            <summary>Reproducible experiment record</summary>
            <JSONView data={detail} />
          </details>
          <Download data={detail} name="cutback-experiment.json" />
        </>
      )}
    </>
  );
}
function RunEvidence({ detail }: { detail: RecordData }) {
  const result = detail.results;
  if (!result?.models)
    return <p>Operation recorded. Review the updated status above.</p>;
  return (
    <>
      <p>
        {result.sample_n ?? result.models[0]?.quality?.sample_n ?? "Unknown"}{" "}
        {result.plan?.split === "validation"
          ? "validation cases"
          : "held-out cases"}{" "}
        ·{" "}
        {result.billing_complete
          ? "Complete modeled call accounting"
          : "Cost completeness unknown"}
      </p>
      {result.search_frontier?.candidates?.length > 0 && (
        <section aria-label="Optimization trade-offs">
          <h3>Cost, quality and latency trade-offs</h3>
          <p>
            These are comparisons on the same sample. Frontier candidates pass
            the declared sample gates and are not dominated on all three
            measures. They still need independent confirmation and staging.
            Unknown measurements stay unknown; production savings are unknown.
          </p>
          <div className="table-scroll" tabIndex={0}>
            <table>
              <caption>Optimization candidate trade-offs</caption>
              <thead>
                <tr>
                  {[
                    "Candidate",
                    "Output limit",
                    "Minimum quality score",
                    "Modeled cost / request",
                    "Mean latency",
                    "Result",
                  ].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.search_frontier.candidates.map((row: RecordData) => (
                  <tr key={row.key}>
                    <th scope="row">
                      {row.model}
                      <br />
                      <span className="hint">{row.key}</span>
                    </th>
                    <td>{row.max_output_tokens} tokens</td>
                    <td>
                      {row.sample_quality_min == null
                        ? "Unknown"
                        : Number(row.sample_quality_min).toFixed(4)}
                    </td>
                    <td>{money(row.cost_per_request_usd)}</td>
                    <td>
                      {row.mean_latency_ms == null
                        ? "Unknown"
                        : `${Number(row.mean_latency_ms).toFixed(1)} ms`}
                    </td>
                    <td>
                      {row.baseline
                        ? "Baseline"
                        : !row.eligible
                          ? "Not eligible: " + row.reason.replaceAll("_", " ")
                          : row.on_frontier === true
                            ? "Sample frontier"
                            : row.on_frontier === false
                              ? "Dominated on sample"
                              : "Trade-off evidence incomplete"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <div className="decision-comparison">
        {result.recommendations?.map((item: RecordData) => (
          <article className="row" key={item.candidate.key}>
            <h3>
              {item.candidate.key}: {decisionLabel(item)}
            </h3>
            <p>
              {item.decision_reason?.replaceAll("_", " ") || item.status}{" "}
              {item.failed_criteria?.length
                ? `Failed criteria: ${item.failed_criteria.join(", ")}.`
                : ""}
              {item.insufficient_criteria?.length
                ? ` More evidence required for: ${item.insufficient_criteria.join(", ")}.`
                : ""}
            </p>
            <p>
              Modeled sample difference:{" "}
              {money(item.sample_modeled_cost_difference_usd)}. Monthly savings
              remain unknown. Production approval is separate.
            </p>
            {item.paired_delta && (
              <p>
                Paired case scores: {item.paired_delta.improved} improved,{" "}
                {item.paired_delta.regressed} regressed,{" "}
                {item.paired_delta.tied} tied. This describes the first repeat
                on this dataset; it is not population verification.
              </p>
            )}
          </article>
        ))}
      </div>
      <div className="table-scroll" tabIndex={0}>
        <table>
          <caption>Paired model evaluation evidence</caption>
          <thead>
            <tr>
              {[
                "Model",
                "Metric",
                "95% interval",
                "Repeated range",
                "Known modeled subtotal",
                "Evidence",
              ].map((label) => (
                <th key={label}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.models.map((row: RecordData) => (
              <tr key={row.model.key}>
                <th scope="row">
                  {row.model.model}
                  <br />
                  <span className="hint">{row.model.key}</span>
                </th>
                <td>
                  {row.quality.value === null
                    ? "Unknown"
                    : Number(row.quality.value).toFixed(4)}
                </td>
                <td>
                  {row.quality.confidence_95?.low != null &&
                  row.quality.confidence_95?.high != null
                    ? `${row.quality.confidence_95.low.toFixed(4)} – ${row.quality.confidence_95.high.toFixed(4)}`
                    : "Unknown"}
                </td>
                <td>
                  {Number(row.metric_min).toFixed(4)} –{" "}
                  {Number(row.metric_max).toFixed(4)}
                </td>
                <td>{money(row.known_cost_subtotal_usd)}</td>
                <td>
                  Tier {row.quality.evidence_tier ?? "unknown"}
                  <br />
                  {row.cost_evidence?.replaceAll("_", " ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">
        Aggregate repeated metrics, judge diagnostics, uncertainty assumptions
        and frozen contracts are included in the downloadable record. Case
        outputs require a separate authorized encrypted-content read. Synthetic
        and reference-only results do not verify customer business quality.
      </p>
      {result.models.map((row: RecordData) => (
        <section key={row.model.key}>
          {!!row.criteria?.length && (
            <>
              <h3>{row.model.key}: quality scorecard</h3>
              <div className="table-scroll" tabIndex={0}>
                <table>
                  <caption>Criteria for {row.model.key}</caption>
                  <thead>
                    <tr>
                      <th>Criterion</th>
                      <th>Score</th>
                      <th>Minimum</th>
                      <th>Required</th>
                      <th>Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {row.criteria.map((item: RecordData) => (
                      <tr key={item.key}>
                        <th scope="row">{item.key}</th>
                        <td>{item.value.toFixed(3)}</td>
                        <td>{item.minimum}</td>
                        <td>{item.required ? "Yes" : "No"}</td>
                        <td>
                          {criterionLabel(item)}
                          <br />
                          <span className="hint">
                            {item.sample_n ?? "Unknown"} cases ·{" "}
                            {item.independent_group_n ?? "Unknown"} groups
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {!!row.slices?.length && (
            <details>
              <summary>
                Results by ticket category or segment: {row.model.key}
              </summary>
              <table>
                <thead>
                  <tr>
                    <th>Segment</th>
                    <th>Cases</th>
                    <th>Score</th>
                    <th>Failures</th>
                  </tr>
                </thead>
                <tbody>
                  {row.slices.map((item: RecordData) => (
                    <tr key={item.stratum}>
                      <th scope="row">{item.stratum}</th>
                      <td>{item.sample_n}</td>
                      <td>{item.value.toFixed(3)}</td>
                      <td>{item.failures}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </section>
      ))}
    </>
  );
}
function Changes({
  items,
  patches,
  rollouts,
  api,
  base,
  reload,
  admin,
  analyst,
}: {
  items: RecordData[];
  patches: RecordData[];
  rollouts: RecordData[];
  api: RequestAPI;
  base: string;
  reload: () => void;
  admin: boolean;
  analyst: boolean;
}) {
  const [message, setMessage] = useState("");
  async function review(id: string, decision: string) {
    try {
      await api(base + `/recommendations/${id}/review`, {
        method: "POST",
        body: JSON.stringify({ decision, reason: "dashboard-review" }),
      });
      reload();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }
  return (
    <>
      <p>
        Review evidence before generating a patch. Acceptance here approves
        staging exploration; production traffic requires a separate operator
        decision.
      </p>
      {!items.length ? (
        <Empty>
          No recommendations. Complete an approved experiment first.
        </Empty>
      ) : (
        items.map((r) => (
          <article className="row" key={r.id}>
            <h2>{r.evidence.candidate?.model || r.id}</h2>
            <p>
              {r.status} · tier {r.evidence.evidence_tier} ·{" "}
              {r.evidence.sample_n} held-out samples
            </p>
            <p>
              {r.evidence.experiment_name ||
                r.evidence.workflow ||
                "Workflow comparison"}{" "}
              · {decisionLabel(r.evidence)}
            </p>
            <p>
              Modeled difference on this sample:{" "}
              {money(r.evidence.sample_modeled_cost_difference_usd)}. Monthly
              savings: unknown.
            </p>
            {!!r.evidence.failed_criteria?.length && (
              <p>Failed: {r.evidence.failed_criteria.join(", ")}</p>
            )}
            <p>
              Rollback target:{" "}
              {r.evidence.rollback?.baseline?.model || "Review frozen baseline"}
              . Evidence applies to this dataset and configuration.
            </p>
            <details>
              <summary>Frozen decision and audit references</summary>
              <JSONView data={r.evidence} />
            </details>
            {analyst && r.status === "pending" && (
              <div className="actions">
                <button
                  disabled={
                    !r.evidence.quality_gate_passed ||
                    !r.evidence.cheaper_on_sample ||
                    r.evidence.evaluator_version !== "deterministic-v3"
                  }
                  onClick={() => review(r.id, "accepted_for_staging")}
                >
                  Accept for staging review
                </button>
                <button onClick={() => review(r.id, "rejected")}>Reject</button>
              </div>
            )}
            <p>
              Generate a source-checked dry-run patch with{" "}
              <code>cutback recommendations patch</code>.
            </p>
            {admin &&
              patches
                .filter((p) => p.recommendation_id === r.id)
                .map((p) => (
                  <PatchReview key={p.id} api={api} base={base} change={p} />
                ))}
            <Download data={r} name={`recommendation-${r.id}.json`} />
          </article>
        ))
      )}
      {admin && (
        <Rollouts
          api={api}
          base={base}
          items={rollouts || []}
          recommendations={items}
          reload={reload}
          admin={admin}
        />
      )}{" "}
      {message && <p role="alert">{message}</p>}
    </>
  );
}
function Integrations({ health }: { health: RecordData }) {
  return (
    <>
      <h2>Connect an application</h2>
      <p>
        Use <strong>Agent workflow</strong> for automatic preparation inside the
        application. The steps below are the manual runtime configuration
        reference.
      </p>
      <ol className="integration-steps">
        <li>
          <strong>Discover the code.</strong> Run{" "}
          <code>cutback scan . --output .cutback/inventory.json</code> in your
          application. Review the source sites in Inventory after uploading the
          scan.
        </li>
        <li>
          <strong>Create a scoped ingestion key.</strong> Use Security. Set
          CUTBACK_PROJECT_ID, CUTBACK_PROJECT_TOKEN, CUTBACK_ENDPOINT,
          CUTBACK_SERVICE_NAME and CUTBACK_ENVIRONMENT in the application's
          secret environment. Provider credentials stay with your application.
        </li>
        <li>
          <strong>Review instrumentation.</strong> Run{" "}
          <code>cutback instrument --target . --plan --json</code>. Review the
          source map and initialization diff; use --apply only after approval.
          Restart after source changes. Use --handler for serverless shutdown.
        </li>
        <li>
          <strong>Verify one owned request.</strong> Enable CUTBACK_ENABLED=true
          and execute an approved request. Check exported and dropped counts
          below, then inspect application-purpose Costs and source attribution
          in Inventory.
        </li>
      </ol>
      <p>
        Metadata capture is the default. Runtime delivery is bounded and best
        effort. A missing health report can mean disabled SDK, no calls, failed
        export, or an exited process.
      </p>
      <details>
        <summary>Already use OpenTelemetry?</summary>
        <p>
          Reuse a filtered metadata-only OTLP/HTTP JSON export with a scoped
          ingestion key. Keep your tracing backend and use one observation path
          per call. Missing provenance stays unknown; external spans can
          aggregate retries.
        </p>
        <a href="https://github.com/CutbackAI/cutback/blob/v0.1.0.dev4/docs/otel-ingestion.md">
          OTLP metadata profile and independent producer example
        </a>
      </details>
      <h2>Instrumentation health</h2>
      <p>
        Supported adapters: OpenAI 2.29.0 and Anthropic 2.3.0. Uninstrumented
        traffic and population coverage remain unknown.
      </p>
      {!health.instances?.length ? (
        <Empty>
          No SDK health reports received. Check endpoint, scoped key, SDK
          initialization, and flush behavior.
        </Empty>
      ) : (
        <div className="table-scroll" tabIndex={0}>
          <table>
            <caption>
              Reported SDK instances (counters since process startup)
            </caption>
            <thead>
              <tr>
                <th>Instance</th>
                <th>Last report</th>
                <th>Observed</th>
                <th>Exported</th>
                <th>Dropped</th>
                <th>Queue</th>
              </tr>
            </thead>
            <tbody>
              {health.instances.map((h: RecordData) => (
                <tr key={h.instance_id}>
                  <td>
                    <code>{h.instance_id}</code>
                  </td>
                  <td>
                    {h.updated_at}
                    <br />
                    {Date.now() - Date.parse(h.updated_at) > 300000
                      ? "Stale snapshot; process state unknown"
                      : "Recent report"}
                  </td>
                  <td>{count(h.counters.observed)}</td>
                  <td>{count(h.counters.exported)}</td>
                  <td>{count(h.counters.dropped)}</td>
                  <td>{count(h.counters.queue_depth)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
function Security({
  data,
  api,
  base,
  reload,
  slug,
  deleted,
}: {
  data: RecordData;
  api: RequestAPI;
  base: string;
  reload: () => void;
  slug: string;
  deleted: () => Promise<void>;
}) {
  const [message, setMessage] = useState("");
  async function revoke(path: string) {
    try {
      await api(path, { method: "DELETE" });
      reload();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }
  return (
    <>
      <p className="notice">
        Admin scope required. Content capture is denied unless both server
        storage and an explicit scoped policy are enabled. This browser does not
        store provider keys.
      </p>
      <h2>Capture policies</h2>
      {!data.policies.length ? (
        <Empty>No policies. Payload capture is disabled.</Empty>
      ) : (
        data.policies.map((p: RecordData) => (
          <details key={p.id}>
            <summary>
              {p.id} · {p.enabled ? "Enabled" : "Revoked"}
            </summary>
            <JSONView data={p} />
            {p.enabled && (
              <button
                onClick={() => revoke(base + "/capture-policies/" + p.id)}
              >
                Revoke capture policy
              </button>
            )}
          </details>
        ))
      )}
      <PolicyForm api={api} base={base} reload={reload} />
      <h2>Project keys</h2>
      {!data.keys.length ? (
        <Empty>No scoped keys.</Empty>
      ) : (
        data.keys.map((k: RecordData) => (
          <article className="row" key={k.id}>
            <p>
              {k.role} · {k.id} · expires {k.expires_at}
            </p>
            <button
              disabled={!!k.revoked_at}
              onClick={() => revoke(base + "/keys/" + k.id)}
            >
              Revoke key
            </button>
          </article>
        ))
      )}
      <KeyForm api={api} base={base} reload={reload} />
      <p>
        Issue a key with{" "}
        <code>cutback keys create --output protected-file.json</code>; tokens
        are shown only once and should enter a secret store.
      </p>
      <h2>Pricing and overrides</h2>
      <details>
        <summary>Recorded catalog and contract rates</summary>
        <JSONView data={data.prices} />
      </details>
      <RateForm api={api} base={base} reload={reload} />
      <DataControls
        api={api}
        base={base}
        reload={reload}
        settings={data.settings || {}}
        slug={slug}
        deleted={deleted}
      />
      <h2>Encrypted application cache</h2>
      <p className="hint">
        Explicit app integration, scoped content authorization and server
        enablement are required. Cache keys must include every tenant, prompt,
        model and freshness dimension.
      </p>
      {!data.cache?.length ? (
        <Empty>No encrypted cache entries.</Empty>
      ) : (
        data.cache.map((entry: RecordData) => (
          <div className="row" key={entry.id}>
            <p>
              <code>{entry.id}</code> · expires {entry.expires_at}
            </p>
            <button
              className="secondary"
              onClick={() => revoke(base + "/cache/" + entry.id)}
            >
              Invalidate cache entry
            </button>
          </div>
        ))
      )}
      <h2>Audit trail</h2>
      {!data.audit.length ? (
        <Empty>No audit records on this page.</Empty>
      ) : (
        <JSONView data={data.audit} />
      )}
      <p>
        Retention defaults: raw events 30 days, daily summaries 395 days,
        approved payloads 7 days. Configure and run the customer-owned worker;
        consult the backup and deletion playbook before destructive operations.
      </p>
      {message && <p role="alert">{message}</p>}
    </>
  );
}

const returned = handleSSOReturn();
createRoot(document.getElementById("root")!).render(
  returned ? (
    <main>
      <h1>Identity sign-in returned</h1>
      <p>
        Return to the original Cutback window. If it was closed, start sign-in
        again; credentials are not stored between pages.
      </p>
    </main>
  ) : (
    <React.StrictMode>
      <App />
    </React.StrictMode>
  ),
);
