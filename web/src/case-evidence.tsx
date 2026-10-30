import React, { useState } from "react";
import { type API, type Data, Form, Field, Check, explain } from "./ui";
import { EpisodeContent } from "./episode-content";

function Correction({
  api,
  base,
  runId,
  row,
  purpose,
}: {
  api: API;
  base: string;
  runId: string;
  row: Data;
  purpose: string;
}) {
  const [created, setCreated] = useState<Data | null>(null);
  return (
    <details className="case-correction">
      <summary>
        {row.episode
          ? "Review or revise this episode contract"
          : "Review or correct this label"}
      </summary>
      <p>
        This creates a new dataset version. The original run, outputs and
        approval remain frozen. New experiments require new approval.
      </p>
      <Form
        title={
          row.episode
            ? "Revised episode dataset version"
            : "Corrected dataset version"
        }
        submit="Create corrected dataset"
        onSubmit={async (data) => {
          const gold = row.episode ? null : JSON.parse(String(data.gold));
          if (
            !row.episode &&
            (gold === null || !["string", "object"].includes(typeof gold))
          )
            throw new Error(
              "Supply a JSON string, object or array as the label.",
            );
          const result = await api(
            `${base}/experiments/${runId}/cases/correct`,
            {
              method: "POST",
              body: JSON.stringify({
                purpose,
                case_id: row.id,
                ...(row.episode
                  ? { episode: JSON.parse(String(data.episode)) }
                  : { gold }),
                correction_ref: data.reference,
                all_labels_reviewed: !!data.reviewed,
              }),
            },
          );
          setCreated(result);
          return "Created a new dataset. The original evidence is unchanged. Open Experiments to review and plan a new comparison.";
        }}
      >
        <label>
          {row.episode
            ? "Revised episode contract as JSON"
            : "Correct label as JSON"}
          <textarea
            name={row.episode ? "episode" : "gold"}
            required
            maxLength={64000}
            defaultValue={JSON.stringify(
              row.episode ?? row.gold ?? "",
              null,
              2,
            )}
          />
        </label>
        <Field
          label="Correction review reference"
          name="reference"
          required
          pattern="[A-Za-z0-9_.:/-]+"
          maxLength={200}
        />
        {!row.episode && (
          <Check
            name="reviewed"
            label="I reviewed all labels in the new dataset as independent ground truth"
          />
        )}
      </Form>
      {created && (
        <p>
          New dataset: <code>{created.id}</code>. Representativeness must be
          reviewed again.
        </p>
      )}
    </details>
  );
}

export function CaseEvidence({
  api,
  base,
  runId,
  models = [],
  strata = [],
}: {
  api: API;
  base: string;
  runId: string;
  models?: string[];
  strata?: string[];
}) {
  const [rows, setRows] = useState<Data[]>([]);
  const [error, setError] = useState("");
  const [purpose, setPurpose] = useState("");
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(true);
  const [model, setModel] = useState("");
  const [stratum, setStratum] = useState("");
  const [legacy, setLegacy] = useState(0);
  const [legacyRows, setLegacyRows] = useState<Data[]>([]);
  const [legacyOffset, setLegacyOffset] = useState(0);
  const [legacyMore, setLegacyMore] = useState(false);
  async function readLegacy(next: number) {
    try {
      const result = await api(
        `${base}/experiments/${runId}/cases/content?legacy_only=true&offset=${next}&limit=25`,
        { method: "POST", body: JSON.stringify({ purpose }) },
      );
      setLegacyRows(result.cases);
      setLegacyOffset(next);
      setLegacyMore(result.more_possible);
      setError("");
    } catch (e) {
      setError(explain(e));
    }
  }
  async function read(next: number) {
    setBusy(true);
    try {
      const query = new URLSearchParams({
        offset: String(next),
        limit: "5",
        failed_only: String(failed),
      });
      if (model) query.set("model_key", model);
      if (stratum) query.set("stratum", stratum);
      const result = await api(
        `${base}/experiments/${runId}/cases/paired?${query}`,
        { method: "POST", body: JSON.stringify({ purpose }) },
      );
      setRows(result.cases);
      setOffset(next);
      setMore(result.more_possible);
      setLegacy(result.unindexed_legacy_outputs);
      setLoaded(true);
      setError("");
    } catch (e) {
      setError(explain(e));
      setRows([]);
    } finally {
      setBusy(false);
    }
  }
  function changed() {
    setRows([]);
    setLoaded(false);
    setOffset(0);
    setMore(false);
  }
  return (
    <section className="case-review" aria-label="Encrypted case review">
      <h3>Inspect encrypted case evidence</h3>
      <p>
        Start with failures and compare every configuration on the same input.
        Admin access, an active policy and an audited purpose are required.
        Content stays in page memory.
      </p>
      <div className="case-controls">
        <label>
          Show cases
          <select
            value={failed ? "failed" : "all"}
            onChange={(e) => {
              setFailed(e.target.value === "failed");
              changed();
            }}
          >
            <option value="failed">Failed checks first</option>
            <option value="all">All captured cases</option>
          </select>
        </label>
        <label>
          Candidate filter
          <select
            value={model}
            onChange={(e) => {
              setModel(e.target.value);
              changed();
            }}
          >
            <option value="">Every configuration</option>
            {models.map((key) => (
              <option key={key}>{key}</option>
            ))}
          </select>
        </label>
        <label>
          Segment filter
          <select
            value={stratum}
            onChange={(e) => {
              setStratum(e.target.value);
              changed();
            }}
          >
            <option value="">Every segment</option>
            {strata.map((key) => (
              <option key={key}>{key}</option>
            ))}
          </select>
        </label>
      </div>
      <label>
        Purpose for reading case evidence
        <input
          value={purpose}
          maxLength={200}
          onChange={(e) => setPurpose(e.target.value)}
          placeholder="sample-review"
        />
      </label>
      <button
        disabled={busy || !/^[A-Za-z0-9_.:/-]{3,200}$/.test(purpose)}
        onClick={() => read(0)}
      >
        {busy ? "Reading authorized evidence…" : "Read authorized case outputs"}
      </button>
      {error && <p role="alert">{error}</p>}
      {legacy > 0 && (
        <p className="notice">
          {legacy} older outputs have no failure index. Use the older-output
          browser below; this filter cannot establish their failure coverage.
        </p>
      )}
      {legacy > 0 && (
        <section>
          <button
            disabled={!/^[A-Za-z0-9_.:/-]{3,200}$/.test(purpose)}
            onClick={() => readLegacy(0)}
          >
            Inspect older unindexed outputs
          </button>
          <p>
            Older records cannot establish failure-first coverage or complete
            case pairing.
          </p>
          {legacyRows.map((row, i) => (
            <details key={i}>
              <summary>
                {row.case?.id || "Redacted case"} ·{" "}
                {row.model_key || "Unknown configuration"}
              </summary>
              <pre tabIndex={0}>{JSON.stringify(row, null, 2)}</pre>
            </details>
          ))}
          {legacyOffset > 0 && (
            <button onClick={() => readLegacy(Math.max(0, legacyOffset - 25))}>
              Previous older outputs
            </button>
          )}
          {legacyMore && (
            <button onClick={() => readLegacy(legacyOffset + 25)}>
              Next older outputs
            </button>
          )}
        </section>
      )}
      {loaded && rows.length === 0 && (
        <p className="empty">
          No indexed cases match this selection. This does not establish that
          unobserved or unretained cases passed.
        </p>
      )}
      {rows.map((item, index) => (
        <details
          className="paired-case"
          key={item.case.id ?? index}
          open={index === 0}
        >
          <summary>
            {item.case.id || "Redacted case"} · {item.outputs.length}{" "}
            configuration outputs
          </summary>
          <div className="case-input">
            <h4>Input and expected result</h4>
            <p>{item.case.input ?? "Input not retained under policy"}</p>
            <p>
              Expected:{" "}
              <code>
                {JSON.stringify(
                  item.case.gold ?? item.case.reference ?? "Unknown",
                )}
              </code>
            </p>
          </div>
          <div className="paired-outputs">
            {item.outputs.map((row: Data, i: number) => (
              <section key={`${row.model_key}:${row.repeat}:${i}`}>
                <h4>
                  {row.model_key ?? "Redacted configuration"} · repeat{" "}
                  {(row.repeat ?? 0) + 1}
                </h4>
                {row.output?.schema_version ===
                "cutback.episode-observation.v1" ? (
                  <EpisodeContent
                    value={row.output}
                    assessment={row.episode_assessment}
                    judgeOutput={row.episode_judge_output}
                  />
                ) : (
                  <pre tabIndex={0}>
                    {"output" in row
                      ? row.output == null
                        ? "Incomplete response"
                        : typeof row.output === "string"
                          ? row.output
                          : JSON.stringify(row.output, null, 2)
                      : "Output not retained under policy"}
                  </pre>
                )}
                {row.compression && (
                  <details>
                    <summary>Compressed input for this native request</summary>
                    <p>
                      {row.compression.input_context_bytes} original context
                      bytes; {row.compression.output_context_bytes} compressed
                      bytes. Byte reduction does not establish token savings or
                      semantic equivalence.
                    </p>
                    <pre tabIndex={0}>{row.compression.rendered_input}</pre>
                  </details>
                )}
                {row.retrieval && (
                  <details>
                    <summary>
                      Retrieved passages for this native request
                    </summary>
                    <p>
                      {row.retrieval.context_bytes} context bytes ·{" "}
                      {row.retrieval.scored_chunks} scored chunks. These
                      retained input passages do not establish independent
                      groundedness.
                    </p>
                    {row.retrieval.documents?.map(
                      (document: Data, index: number) => (
                        <section key={document.document_id + ":" + index}>
                          <h5>Document {index + 1}</h5>
                          <p>
                            <code>{document.document_id}</code>
                          </p>
                          <pre tabIndex={0}>{document.text}</pre>
                        </section>
                      ),
                    )}
                  </details>
                )}
                {row.criteria?.map((criterion: Data) => (
                  <p
                    className={
                      criterion.value == null
                        ? ""
                        : criterion.value < criterion.minimum
                          ? "criterion-failed"
                          : "criterion-passed"
                    }
                    key={criterion.key}
                  >
                    {criterion.key}:{" "}
                    {criterion.value == null
                      ? "Unknown"
                      : criterion.value >= criterion.minimum
                        ? "Pass"
                        : "Fail"}{" "}
                    ({criterion.value}; minimum {criterion.minimum})
                  </p>
                ))}
              </section>
            ))}
          </div>
          {item.case.id && (
            <Correction
              api={api}
              base={base}
              runId={runId}
              row={item.case}
              purpose={purpose}
            />
          )}
        </details>
      ))}
      <div className="pager">
        {offset > 0 && (
          <button disabled={busy} onClick={() => read(Math.max(0, offset - 5))}>
            Previous cases
          </button>
        )}
        {more && (
          <button disabled={busy} onClick={() => read(offset + 5)}>
            Next cases
          </button>
        )}
      </div>
    </section>
  );
}
