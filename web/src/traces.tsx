import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { explain, money, type API } from "./ui";
import { millis, traceRows, traceTiming } from "./trace-layout";
import { SpanContent } from "./trace-content";
import { CompareTraces } from "./trace-comparison";
type TracePage = components["schemas"]["TracePage"];
type SessionPage = components["schemas"]["SessionPage"];
type Detail = components["schemas"]["TraceDetail"];
type Evaluation = components["schemas"]["AssertedEvaluation"];

function Evaluations({
  items,
  target = "trace",
}: {
  items: Evaluation[];
  target?: string;
}) {
  return (
    <section aria-label={"External evaluation results for " + target}>
      <h3>Evaluation evidence</h3>
      <p>
        External scores are asserted by their submitting evaluator. Technical
        contract checks and model judge assessments retain their own evidence
        labels. None alone verifies independent task success.
      </p>
      {!items.length ? (
        <p>No external results for this target.</p>
      ) : (
        <div
          className="table-scroll"
          tabIndex={0}
          role="region"
          aria-label={"Scrollable external results for " + target}
        >
          <table>
            <caption>Evaluation metadata and evidence source</caption>
            <thead>
              <tr>
                <th>Evaluator / version</th>
                <th>Outcome</th>
                <th>Score</th>
                <th>Target</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.result_id + ":" + r.evidence}>
                  <td>
                    {r.evaluator} / {r.evaluator_version}
                  </td>
                  <td>
                    {r.outcome}
                    {r.error_code ? " · " + r.error_code : ""}
                  </td>
                  <td>
                    {r.score === null || r.score === undefined
                      ? "Unknown"
                      : String(r.score)}
                  </td>
                  <td>
                    {r.span_id || (r.session_id ? "Session" : "Trace")}
                    {r.target_in_retained_page === false
                      ? " · target not in retained page"
                      : ""}
                  </td>
                  <td>
                    {r.source} · {r.evidence.replaceAll("_", " ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function Investigation({
  api,
  base,
  id,
  allowContent,
  onReview,
}: {
  api: API;
  base: string;
  id: string;
  allowContent: boolean;
  onReview?: (contentID: string) => void;
}) {
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [status, setStatus] = useState("all");
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setData(null);
    setError("");
    api(base + "/traces/" + encodeURIComponent(id), {
      signal: controller.signal,
    })
      .then((value) => {
        if (active) setData(value);
      })
      .catch((e) => {
        if (active) setError(explain(e));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [api, base, id, revision]);
  if (error)
    return (
      <div role="alert">
        Could not load this trace: {error}.{" "}
        <button onClick={() => setRevision(revision + 1)}>Retry trace</button>
      </div>
    );
  if (!data) return <p role="status">Loading trace…</p>;
  const timing = traceTiming(data.spans);
  const rows = traceRows(data.spans).filter(
    ({ span }) => status === "all" || span.status === status,
  );
  return (
    <section className="trace-investigation" aria-label="Trace investigation">
      <h2>Trace investigation</h2>
      <p className="trace-identity">{data.trace_id}</p>
      <p>
        {data.returned_spans} retained spans · {data.observed_execution_errors}{" "}
        observed execution errors · Business success: unknown
      </p>
      <p>
        Known LLM cost in this page: {money(data.page_known_llm_cost_usd)}. Full
        trace cost and capture coverage are unknown. Generic spans do not enable
        replay.
      </p>
      <div className="notice">
        <strong>Evidence limits</strong>
        <ul>
          {data.integrity.map((flag) => (
            <li key={flag}>{flag.replaceAll("_", " ")}</li>
          ))}
        </ul>
        {data.truncated && (
          <p>
            This trace exceeds the 1,000-span investigation bound. Parent links
            and counts describe this retained page only.
          </p>
        )}
      </div>
      <label>
        Execution status{" "}
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="all">All retained statuses</option>
          <option value="error">Errors</option>
          <option value="unknown">Unknown</option>
          <option value="ok">OK</option>
          <option value="incomplete">Incomplete</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </label>
      {!rows.length && <p>No retained spans match this status.</p>}
      {!!rows.length && (
        <div
          className="table-scroll"
          tabIndex={0}
          role="region"
          aria-label="Scrollable trace waterfall"
        >
          <table className="trace-waterfall">
            <caption>
              Trace tree and waterfall — observed intervals, not summed latency
            </caption>
            <thead>
              <tr>
                <th>Operation / parent</th>
                <th>Kind / service</th>
                <th>Execution status</th>
                <th>Interval</th>
                <th>Native cost evidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ span, depth }) => {
                const offset = BigInt(span.started_ns) - timing.origin;
                const duration = span.ended_ns
                  ? BigInt(span.ended_ns) - BigInt(span.started_ns)
                  : null;
                const x = Number((offset * 1000n) / timing.width),
                  width =
                    duration !== null
                      ? Math.max(2, Number((duration * 1000n) / timing.width))
                      : 2;
                return (
                  <tr key={span.span_id}>
                    <td>
                      <span aria-hidden="true">
                        {"↳ ".repeat(Math.min(depth, 4))}
                      </span>
                      <strong>{span.operation}</strong>
                      <details>
                        <summary>Span identity</summary>
                        <p>{span.span_id}</p>
                        <p>Parent: {span.parent_span_id || "Root"}</p>
                        <p>Workflow: {span.workflow || "Unknown"}</p>
                        <p>Release: {span.release || "Unknown"}</p>
                        <p>Session: {span.session_id || "Unknown"}</p>
                      </details>
                      {span.content_available &&
                        (allowContent ? (
                          <SpanContent
                            api={api}
                            path={
                              base +
                              "/traces/" +
                              encodeURIComponent(id) +
                              "/spans/" +
                              span.span_id +
                              "/content"
                            }
                          />
                        ) : (
                          <p>
                            Encrypted content requires project administrator
                            access.
                          </p>
                        ))}
                      {allowContent && span.content_id && onReview && (
                        <button onClick={() => onReview(span.content_id!)}>
                          Prepare review of this step
                        </button>
                      )}
                      {span.issues.map((issue) => (
                        <p className="criterion-failed" key={issue}>
                          {issue.replaceAll("_", " ")}
                        </p>
                      ))}
                    </td>
                    <td>
                      {span.kind}
                      <br />
                      {span.service}
                    </td>
                    <td>{span.status}</td>
                    <td>
                      <svg
                        viewBox="0 0 1002 24"
                        role="img"
                        aria-label={`Starts ${millis(offset)} milliseconds after first observed span; duration ${duration === null ? "unknown" : millis(duration) + " milliseconds"}`}
                      >
                        <rect
                          x={x}
                          y="3"
                          width={width}
                          height="18"
                          className={
                            span.status === "error"
                              ? "waterfall-error"
                              : "waterfall-bar"
                          }
                        />
                      </svg>
                      <span>
                        +{millis(offset)} ms ·{" "}
                        {duration === null
                          ? "Duration unknown"
                          : millis(duration) + " ms"}
                      </span>
                    </td>
                    <td>
                      {span.native ? (
                        <>
                          {money(span.native.cost_usd)}
                          <br />
                          {span.native.status.replaceAll("_", " ")}
                          <br />
                          {span.native.observations} observations
                        </>
                      ) : (
                        "No native LLM correlation"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <Evaluations items={data.evaluations} />
      {data.evaluations_truncated && (
        <p className="notice">
          External result display is truncated at 200 results.
        </p>
      )}
    </section>
  );
}

export function Traces({
  api,
  project,
  allowContent,
  onReview,
}: {
  api: API;
  project: string;
  allowContent: boolean;
  onReview?: (contentID: string) => void;
}) {
  const base = "/projects/" + encodeURIComponent(project);
  const [environments, setEnvironments] = useState<
    { id: string; name: string }[]
  >([]);
  const [envCursor, setEnvCursor] = useState<string | null>(null);
  const [env, setEnv] = useState("");
  const [mode, setMode] = useState("traces");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [session, setSession] = useState<{
    id: string;
    session_id: string;
  } | null>(null);
  const [cursor, setCursor] = useState("");
  const [storedPage, setPage] = useState<{
    key: string;
    value: TracePage | SessionPage;
  } | null>(null);
  const [sessionResults, setSessionResults] = useState<Evaluation[]>([]);
  const [sessionResultsCursor, setSessionResultsCursor] = useState<
    string | null
  >(null);
  const [selected, setSelected] = useState("");
  const [comparison, setComparison] = useState("");
  const [filters, setFilters] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const requestKey = JSON.stringify([
    env,
    mode,
    session?.id,
    cursor,
    errorsOnly,
    revision,
    filters,
  ]);
  const currentKey = useRef(requestKey);
  currentKey.current = requestKey;
  const page = storedPage?.key === requestKey ? storedPage.value : null;
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    api(base + "/environments", { signal: controller.signal })
      .then((value) => {
        if (!active) return;
        setEnvironments(value.items);
        setEnvCursor(value.next_cursor);
        setEnv((previous) =>
          value.items.some((r: { id: string }) => r.id === previous)
            ? previous
            : value.items[0]?.id || "",
        );
        setLoading(false);
      })
      .catch((e) => {
        if (active) {
          setError(explain(e));
          setLoading(false);
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [api, base, revision]);
  useEffect(() => {
    if (!env) return;
    let active = true;
    const controller = new AbortController();
    setPage(null);
    setSelected("");
    setComparison("");
    setError("");
    setLoading(true);
    setSessionResults([]);
    setSessionResultsCursor(null);
    const query =
      "?environment_id=" +
      encodeURIComponent(env) +
      (mode === "traces" && filters ? "&" + filters : "") +
      (errorsOnly ? "&errors_only=true" : "") +
      (cursor ? "&before=" + encodeURIComponent(cursor) : "") +
      (session ? "&session_record_id=" + encodeURIComponent(session.id) : "");
    Promise.all([
      api(base + "/" + mode + query, { signal: controller.signal }),
      session
        ? api(
            base +
              "/sessions/" +
              encodeURIComponent(session.id) +
              "/evaluations",
            { signal: controller.signal },
          )
        : Promise.resolve(null),
    ])
      .then(([value, evaluations]) => {
        if (active) {
          setPage({ key: requestKey, value });
          setSessionResults(evaluations?.items || []);
          setSessionResultsCursor(evaluations?.next_cursor || null);
        }
      })
      .catch((e) => {
        if (active) setError(explain(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [
    api,
    base,
    env,
    mode,
    session,
    cursor,
    revision,
    errorsOnly,
    requestKey,
    filters,
  ]);
  async function moreEnvironments() {
    try {
      const value = await api(
        base + "/environments?before=" + encodeURIComponent(envCursor!),
      );
      setEnvironments((previous) => [...previous, ...value.items]);
      setEnvCursor(value.next_cursor);
    } catch (e) {
      setError(explain(e));
    }
  }
  async function moreEvaluations() {
    const version = requestKey;
    try {
      const value = await api(
        base +
          "/sessions/" +
          encodeURIComponent(session!.id) +
          "/evaluations?before=" +
          encodeURIComponent(sessionResultsCursor!),
      );
      if (currentKey.current !== version) return;
      setSessionResults((previous) => [...previous, ...value.items]);
      setSessionResultsCursor(value.next_cursor);
    } catch (e) {
      setError(explain(e));
    }
  }
  return (
    <section aria-label="Workflow traces and sessions">
      <p>
        Follow agent, retrieval, tool and LLM steps without capturing
        application content. Execution status and external scores remain
        separate evidence.
      </p>
      <div className="toolbar">
        <label>
          Trace environment{" "}
          <select
            value={env}
            onChange={(e) => {
              setEnv(e.target.value);
              setCursor("");
              setSession(null);
            }}
          >
            <option value="" disabled>
              Select an environment
            </option>
            {environments.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        {envCursor && (
          <button onClick={moreEnvironments}>Load more environments</button>
        )}
        <label className="checkbox">
          <input
            type="checkbox"
            checked={errorsOnly}
            onChange={(e) => {
              setErrorsOnly(e.target.checked);
              setCursor("");
            }}
          />
          Only executions with errors
        </label>
        <button
          aria-pressed={mode === "traces"}
          onClick={() => {
            setMode("traces");
            setCursor("");
            setSession(null);
          }}
        >
          Browse traces
        </button>
        <button
          aria-pressed={mode === "sessions"}
          onClick={() => {
            setMode("sessions");
            setCursor("");
            setSession(null);
          }}
        >
          Browse sessions
        </button>
        <button onClick={() => setRevision(revision + 1)}>
          Refresh investigation
        </button>
      </div>
      {mode === "traces" && (
        <details>
          <summary>Filter retained traces</summary>
          <form
            className="form-grid"
            aria-label="Trace search"
            onSubmit={(e) => {
              e.preventDefault();
              const values = new FormData(e.currentTarget);
              const query = new URLSearchParams();
              for (const name of [
                "workflow",
                "service",
                "release",
                "model",
                "start",
                "end",
              ]) {
                const value = String(values.get(name) || "").trim();
                if (value)
                  query.set(
                    name,
                    name === "start" || name === "end"
                      ? new Date(value).toISOString()
                      : value,
                  );
              }
              setFilters(query.toString());
              setCursor("");
            }}
          >
            <label>
              Workflow identifier
              <input name="workflow" maxLength={200} />
            </label>
            <label>
              Service identifier
              <input name="service" maxLength={200} />
            </label>
            <label>
              Release identifier
              <input name="release" maxLength={200} />
            </label>
            <label>
              Observed native model
              <input name="model" maxLength={200} />
            </label>
            <label>
              Span start from (local time)
              <input type="datetime-local" name="start" />
            </label>
            <label>
              Span start before (local time)
              <input type="datetime-local" name="end" />
            </label>
            <button>Apply trace filters</button>
            <button
              type="reset"
              onClick={() => {
                setFilters("");
                setCursor("");
              }}
            >
              Clear trace filters
            </button>
          </form>
          <p>
            All step filters must match one retained step. Model matches use
            exact native observation identity. Filters do not establish
            population coverage.
          </p>
        </details>
      )}
      {loading && <p role="status">Loading workflow evidence…</p>}
      {error && (
        <p role="alert">
          Could not load workflow evidence: {error}.{" "}
          <button onClick={() => setRevision(revision + 1)}>
            Retry evidence
          </button>
        </p>
      )}
      {!loading && !error && !environments.length && (
        <p>No environments are configured.</p>
      )}
      {session && (
        <>
          <h2>Session: {session.session_id}</h2>
          <button
            onClick={() => {
              setSession(null);
              setCursor("");
            }}
          >
            Show all traces
          </button>
          <Evaluations items={page ? sessionResults : []} target="session" />
          {sessionResultsCursor && (
            <button onClick={moreEvaluations}>
              Load more session evaluations
            </button>
          )}
        </>
      )}
      {page && !page.items.length && (
        <p>
          No retained {mode}
          {session ? " for this session" : " in this environment"}. Run the
          synthetic workflow example to add a trace.
        </p>
      )}
      {page && !!page.items.length && (
        <div
          className="table-scroll"
          tabIndex={0}
          role="region"
          aria-label="Scrollable retained evidence list"
        >
          <table>
            <caption>
              {mode === "traces" ? "Retained traces" : "Retained sessions"} —
              newest received first
            </caption>
            <thead>
              <tr>
                <th>{mode === "traces" ? "Trace" : "Session"}</th>
                <th>First received</th>
                <th>
                  {mode === "traces" ? "Retained spans" : "Retained traces"}
                </th>
                <th>Execution errors</th>
                <th>Investigate</th>
              </tr>
            </thead>
            <tbody>
              {mode === "traces"
                ? (page as TracePage).items.map((r) => (
                    <tr key={r.id}>
                      <td className="trace-identity">{r.trace_id}</td>
                      <td>{new Date(r.received_at).toLocaleString()}</td>
                      <td>{r.retained_spans}</td>
                      <td>{r.observed_execution_errors}</td>
                      <td>
                        <button onClick={() => setSelected(r.id)}>
                          Open trace {r.trace_id.slice(0, 8)}
                        </button>
                      </td>
                    </tr>
                  ))
                : (page as SessionPage).items.map((r) => (
                    <tr key={r.id}>
                      <td>{r.session_id}</td>
                      <td>{new Date(r.received_at).toLocaleString()}</td>
                      <td>{r.retained_traces}</td>
                      <td>{r.observed_execution_errors}</td>
                      <td>
                        <button
                          onClick={() => {
                            setSession(r);
                            setMode("traces");
                            setCursor("");
                          }}
                        >
                          Open session {r.session_id}
                        </button>
                      </td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </div>
      )}
      {cursor && (
        <button onClick={() => setCursor("")}>First evidence page</button>
      )}
      {page?.next_cursor && (
        <button onClick={() => setCursor(page.next_cursor!)}>
          Next evidence page
        </button>
      )}
      {selected && page && mode === "traces" && (
        <label>
          Compare selected trace with
          <select
            value={comparison}
            onChange={(e) => setComparison(e.target.value)}
          >
            <option value="">Choose another retained trace</option>
            {(page as TracePage).items
              .filter((r) => r.id !== selected)
              .map((r) => (
                <option key={r.id} value={r.id}>
                  {r.trace_id}
                </option>
              ))}
          </select>
        </label>
      )}
      {selected && comparison && page && (
        <CompareTraces
          key={selected + comparison}
          api={api}
          base={base}
          left={selected}
          right={comparison}
        />
      )}
      {selected && page && (
        <Investigation
          key={selected}
          api={api}
          base={base}
          id={selected}
          allowContent={allowContent}
          onReview={onReview}
        />
      )}
    </section>
  );
}
