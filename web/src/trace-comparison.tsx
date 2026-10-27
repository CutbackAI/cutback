import React, { useEffect, useState } from "react";
import type { components } from "./api.generated";
import { explain, type API } from "./ui";
import { millis } from "./trace-layout";
type Comparison = components["schemas"]["TraceComparison"];

export function CompareTraces({
  api,
  base,
  left,
  right,
}: {
  api: API;
  base: string;
  left: string;
  right: string;
}) {
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setData(null);
    setError("");
    api(
      base +
        "/traces/" +
        encodeURIComponent(left) +
        "/compare/" +
        encodeURIComponent(right),
      { signal: controller.signal },
    )
      .then((value) => {
        if (active) setData(value);
      })
      .catch((err) => {
        if (active) setError(explain(err));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [api, base, left, right, retry]);
  if (error)
    return (
      <p role="alert">
        Could not compare retained paths: {error}.{" "}
        <button onClick={() => setRetry(retry + 1)}>Retry comparison</button>
      </p>
    );
  if (!data) return <p role="status">Loading trace comparison…</p>;
  const one = new Map(data.left.spans.map((s) => [s.span_id, s]));
  const two = new Map(data.right.spans.map((s) => [s.span_id, s]));
  const interval = (s: components["schemas"]["ObservedSpan"] | undefined) =>
    !s
      ? "Not matched"
      : s.status +
        " · " +
        (s.ended_ns
          ? millis(BigInt(s.ended_ns) - BigInt(s.started_ns)) + " ms"
          : "duration unknown");
  return (
    <section aria-label="Retained trace path comparison">
      <h2>Compare execution paths</h2>
      <p>
        Matches use exact retained operation/kind ancestry and occurrence order.
        This is a structural comparison; task quality is not verified. Missing
        ancestors and cycles are not guessed into matches.
      </p>
      {(data.left.truncated || data.right.truncated) && (
        <p className="notice">
          Each side is bounded to 500 spans. This comparison is truncated and
          does not represent complete execution paths.
        </p>
      )}
      <div
        className="table-scroll"
        tabIndex={0}
        role="region"
        aria-label="Scrollable execution path comparison"
      >
        <table>
          <caption>Execution path differences</caption>
          <thead>
            <tr>
              <th>Operation path</th>
              <th>Selected trace</th>
              <th>Comparison trace</th>
              <th>Alignment</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r, i) => (
              <tr key={i}>
                <td>{r.operation_path.join(" → ")}</td>
                <td>{interval(one.get(r.left_span_id || ""))}</td>
                <td>{interval(two.get(r.right_span_id || ""))}</td>
                <td>{r.alignment.replaceAll("_", " ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
