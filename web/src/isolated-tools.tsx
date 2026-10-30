import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, explain, json } from "./ui";

type Sessions = components["schemas"]["ToolSessionPage"];
type Operations = components["schemas"]["ToolOperationPage"];

export function IsolatedToolEvidence({
  api,
  base,
  runId,
  canContain = false,
}: {
  api: API;
  base: string;
  runId: string;
  canContain?: boolean;
}) {
  const [sessions, setSessions] = useState<Sessions | null>(null);
  const [operations, setOperations] = useState<Operations | null>(null);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    generation.current++;
    setSessions(null);
    setOperations(null);
    setSelected("");
    setError("");
    setNotice("");
    setBusy(false);
    return () => {
      generation.current++;
    };
  }, [base, runId]);
  const route = `${base}/experiments/${runId}/tool-sessions`;
  async function load(after?: string) {
    const current = ++generation.current;
    setBusy(true);
    setError("");
    setOperations(null);
    setSelected("");
    try {
      const page = await api(
        `${route}?limit=50${after ? `&after=${after}` : ""}`,
      );
      if (current === generation.current) setSessions(page);
    } catch (failure) {
      if (current === generation.current) setError(explain(failure));
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }
  async function inspect(identifier: string, after = 0) {
    const current = ++generation.current;
    setBusy(true);
    setError("");
    try {
      const page = await api(
        `${route}/${identifier}/operations?limit=50&after=${after}`,
      );
      if (current === generation.current) {
        setSelected(identifier);
        setOperations(page);
      }
    } catch (failure) {
      if (current === generation.current) setError(explain(failure));
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }
  async function contain(row: Sessions["items"][number]) {
    const current = ++generation.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await json(api, `${route}/${row.id}/containment`, {
        approval_hash: row.approval_hash,
      });
      if (current === generation.current) {
        setNotice(
          "Containment requested. The separate configured worker must confirm cleanup. No tool operation will be replayed.",
        );
        const page = await api(`${route}?limit=50`);
        if (current === generation.current) setSessions(page);
      }
    } catch (failure) {
      if (current === generation.current) setError(explain(failure));
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }
  return (
    <section aria-label="Isolated tool receipts">
      <h4>Isolated tool execution</h4>
      <p>
        Review actual scoped record operations and container cleanup separately
        from model charges. Complete task cost remains unknown.
      </p>
      <button disabled={busy} onClick={() => load()}>
        {busy
          ? "Loading tool receipts…"
          : sessions
            ? "Refresh isolated tool receipts"
            : "Load isolated tool receipts"}
      </button>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {sessions && (
        <>
          <p>
            {sessions.retained_sessions} retained sessions. Requests and results
            stay out of these metadata receipts. Missing history cannot
            establish operation coverage.
          </p>
          {!sessions.items.length ? (
            <p>
              No isolated tool sessions for this run. Fixture replay does not
              establish container isolation.
            </p>
          ) : (
            <div className="table-scroll" tabIndex={0}>
              <table>
                <caption>Isolated record sessions</caption>
                <thead>
                  <tr>
                    {[
                      "Session",
                      "State",
                      "Calls",
                      "Containment",
                      "Actions",
                    ].map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sessions.items.map((row) => (
                    <tr key={row.id}>
                      <th scope="row">
                        {row.model_key || "Model unavailable"}
                        <br />
                        Repeat{" "}
                        {row.repeat_index == null
                          ? "Unknown"
                          : row.repeat_index + 1}
                      </th>
                      <td>{row.state}</td>
                      <td>
                        {row.calls_used} / {row.max_calls}
                      </td>
                      <td>
                        {row.cleanup_confirmed
                          ? "Cleanup confirmed"
                          : row.cleanup_requested
                            ? "Containment requested; unconfirmed"
                            : "Cleanup unconfirmed"}
                        <br />
                        {row.cleanup_attempts} / 3 recovery attempts
                      </td>
                      <td>
                        <button
                          disabled={busy}
                          onClick={() => inspect(row.id)}
                          aria-label={`Inspect operations for session ${row.id}`}
                        >
                          Inspect operations
                        </button>
                        {canContain && !row.cleanup_confirmed && (
                          <button disabled={busy} onClick={() => contain(row)}>
                            Request containment
                          </button>
                        )}
                        <details>
                          <summary>Approved isolation identity</summary>
                          <dl>
                            <dt>Session</dt>
                            <dd>
                              <code>{row.id}</code>
                            </dd>
                            <dt>Episode</dt>
                            <dd>
                              <code>{row.episode_id}</code>
                            </dd>
                            <dt>Image</dt>
                            <dd>
                              <code>{row.image_id}</code>
                            </dd>
                            <dt>Tool service</dt>
                            <dd>
                              <code>{row.broker_id}</code>
                            </dd>
                            <dt>Policy digest</dt>
                            <dd>
                              <code>{row.policy_sha256}</code>
                            </dd>
                            <dt>Plan approval</dt>
                            <dd>
                              <code>{row.approval_hash}</code>
                            </dd>
                          </dl>
                        </details>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {sessions.next_after && (
            <button disabled={busy} onClick={() => load(sessions.next_after!)}>
              Next tool sessions
            </button>
          )}
        </>
      )}
      {operations && (
        <div className="table-scroll" tabIndex={0}>
          <table>
            <caption>Tool operations for {selected}</caption>
            <thead>
              <tr>
                {["Sequence", "Tool", "State", "State change"].map((label) => (
                  <th key={label}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {operations.items.map((row) => (
                <tr key={row.id}>
                  <th scope="row">{row.sequence}</th>
                  <td>
                    {row.tool_kind === "records_read"
                      ? "Read authorized fields"
                      : row.tool_kind === "records_patch"
                        ? "Apply approved transition"
                        : "Unapproved tool"}
                    <br />
                    <code>{row.tool_kind}</code>
                  </td>
                  <td>{row.state}</td>
                  <td>
                    {row.state_changed == null
                      ? "Unknown"
                      : row.state_changed
                        ? "Recorded"
                        : "None"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {operations.next_after != null && (
            <button
              disabled={busy}
              onClick={() => inspect(selected, operations.next_after!)}
            >
              Next tool operations
            </button>
          )}
        </div>
      )}
    </section>
  );
}
