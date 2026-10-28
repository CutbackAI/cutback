import React from "react";
import type { Data } from "./ui";

const labels: Record<string, string> = {
  execution_outcome: "Permitted task outcome",
  approved_tool_boundary: "Only declared tools used",
  no_duplicate_effect_attempt: "State-changing actions not repeated",
  allowed_tool_path: "One permitted tool path used",
  declared_user_turn_order: "Scripted user turns in order",
  declared_answer_reference: "Answer matches an accepted reference",
  literal_citation_support: "Quotation belongs to the cited retrieved document",
};

function payload(value: unknown) {
  return value === undefined ? "Not retained" : JSON.stringify(value, null, 2);
}

export function EpisodeContent({
  value,
  assessment,
}: {
  value: Data;
  assessment?: Data;
}) {
  const events: Data[] = Array.isArray(value.events) ? value.events : [];
  return (
    <div className="episode-content">
      <p>
        <strong>Task outcome:</strong> {value.outcome ?? "Unknown"}.{" "}
        <strong>Declared checks:</strong> {assessment?.status ?? "Unknown"}.
      </p>
      <p className="hint">
        Synthetic fixture state and declared references. This does not verify
        production task success or semantic correctness.
      </p>
      <ol
        className="episode-turns"
        aria-label="Conversation and tool trajectory"
      >
        {events.map((event, index) => (
          <li key={index}>
            <strong>
              {event.kind === "user"
                ? "User"
                : event.kind === "assistant"
                  ? "Assistant"
                  : event.kind === "tool"
                    ? `Tool: ${event.tool ?? "Unknown"}`
                    : event.kind === "retrieval"
                      ? "Retrieved documents"
                      : "Retained event"}
            </strong>
            {event.kind === "user" || event.kind === "assistant" ? (
              <>
                {event.text && <p>{event.text}</p>}
                {event.finish === "refused" && <p>Response refused.</p>}
                {event.finish === "incomplete" && (
                  <p>Incomplete response; a final answer is unavailable.</p>
                )}
                {Array.isArray(event.tool_calls) &&
                  event.tool_calls.map((call: Data) => (
                    <div key={call.call_id}>
                      <p>
                        Requested <code>{call.tool}</code>
                      </p>
                      <pre tabIndex={0}>{payload(call.arguments)}</pre>
                    </div>
                  ))}
              </>
            ) : event.kind === "tool" ? (
              <>
                <p>
                  {event.status ?? "Unknown result status"}
                  {event.duplicate_effect_attempt === true
                    ? " · Duplicate effect attempt blocked"
                    : ""}
                </p>
                {Array.isArray(event.result?.documents) ? (
                  <dl>
                    {event.result.documents.map((doc: Data, i: number) => (
                      <React.Fragment key={i}>
                        <dt>
                          <code>{doc.document_id ?? "Unknown document"}</code>
                        </dt>
                        <dd>{doc.text ?? "Text not retained"}</dd>
                      </React.Fragment>
                    ))}
                  </dl>
                ) : (
                  <pre tabIndex={0}>{payload(event.result)}</pre>
                )}
              </>
            ) : event.kind === "retrieval" ? (
              <p>
                {Array.isArray(event.documents)
                  ? event.documents
                      .map((doc: Data) => doc.document_id ?? "Unknown document")
                      .join(", ")
                  : "Document projection not retained"}
              </p>
            ) : (
              <pre tabIndex={0}>{payload(event)}</pre>
            )}
          </li>
        ))}
      </ol>
      {!events.length && (
        <p>
          Conversation events are unavailable under the retained content policy.
        </p>
      )}
      {Array.isArray(value.citations) && value.citations.length > 0 && (
        <>
          <h5>Answer citations</h5>
          {value.citations.map((citation: Data, index: number) => (
            <blockquote key={index}>
              <p>{citation.quote ?? "Quote not retained"}</p>
              <footer>
                Document: <code>{citation.document_id ?? "Unknown"}</code>
              </footer>
            </blockquote>
          ))}
        </>
      )}
      {Array.isArray(assessment?.checks) && (
        <div className="table-scroll" tabIndex={0}>
          <table>
            <caption>Declared task checks</caption>
            <thead>
              <tr>
                <th>Check</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {assessment.checks.map((check: Data) => (
                <tr key={check.key}>
                  <th scope="row">
                    {labels[check.key] ??
                      String(check.key ?? "Unknown check").replaceAll("_", " ")}
                  </th>
                  <td>{check.status ?? "Unknown"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <details>
        <summary>Final synthetic state</summary>
        <pre tabIndex={0}>{payload(value.final_state)}</pre>
      </details>
      <details>
        <summary>Raw authorized episode record</summary>
        <pre tabIndex={0}>{payload(value)}</pre>
      </details>
    </div>
  );
}
