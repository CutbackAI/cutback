import React, { useEffect, useRef, useState } from "react";

type Data = Record<string, any>;
export function SampleGuide({
  demo,
  api,
  navigate,
  evidence,
}: {
  demo: Data;
  api: (path: string, options?: RequestInit) => Promise<any>;
  navigate: (view: string) => void;
  evidence: (detail: Data) => React.ReactNode;
}) {
  const [detail, setDetail] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const selectedByUser = useRef(false);
  const generation = useRef(0);
  const resultHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (detail && selectedByUser.current) {
      resultHeading.current?.focus({ preventScroll: true });
      resultHeading.current?.scrollIntoView({ block: "start" });
    }
  }, [detail]);
  useEffect(() => {
    let active = true;
    const request = ++generation.current;
    setLoading(true);
    api(`/projects/${demo.project_id}/experiments/${demo.comparison_run_id}`)
      .then((value) => {
        if (active && request === generation.current) setDetail(value);
      })
      .catch(() => {
        if (active && request === generation.current)
          setError(
            "Could not load the sample result. Check that the demo is still running.",
          );
      })
      .finally(() => {
        if (active && request === generation.current) setLoading(false);
      });
    return () => {
      active = false;
      generation.current++;
    };
  }, [api, demo.project_id, demo.comparison_run_id]);
  const inspect = async (id: string) => {
    const request = ++generation.current;
    selectedByUser.current = true;
    setDetail(null);
    setLoading(true);
    try {
      const value = await api(`/projects/${demo.project_id}/experiments/${id}`);
      if (request === generation.current) {
        setDetail(value);
        setError("");
      }
    } catch {
      if (request === generation.current)
        setError(
          "Could not load the sample result. Check that the demo is still running.",
        );
    } finally {
      if (request === generation.current) setLoading(false);
    }
  };
  return (
    <section className="sample-guide">
      <p className="eyebrow">GUIDED SAMPLE · NO PAID API CALLS</p>
      <h2>Can this support desk use a cheaper model?</h2>
      <p>
        The app assigns a category and priority to a support ticket. Saving
        money is useful only if urgent tickets still receive urgent priority.
      </p>
      <p className="disclosure">{demo.disclosure}</p>
      <div className="actions sample-actions">
        <button onClick={() => inspect(demo.comparison_run_id)}>
          Compare sample candidates
        </button>
        <button onClick={() => inspect(demo.technical_run_id)}>
          Inspect technical-only result
        </button>
        <button onClick={() => navigate("Changes")}>
          Review source change
        </button>
        <button onClick={() => navigate("Traces")}>
          Investigate a failed session
        </button>
        {demo.task_economics_sample && (
          <button onClick={() => navigate("Task economics")}>
            Inspect task costs and missing coverage
          </button>
        )}
        {demo.improvement_campaign_sample && (
          <button onClick={() => navigate("Campaigns")}>
            Follow an automatic improvement campaign
          </button>
        )}
        {demo.agent_episode_run_id && (
          <button onClick={() => inspect(demo.agent_episode_run_id)}>
            Inspect agent and retrieval tasks
          </button>
        )}
        {demo.retrieval_sample && (
          <button onClick={() => navigate("Retrieval")}>
            Compare real corpus retrieval configurations
          </button>
        )}
      </div>
      {demo.provider_batch_sample && (
        <section aria-label="Native batch ticket sample">
          <h2>Process a three-label task asynchronously</h2>
          <p>
            This synthetic batch runs billing, access and technical tickets
            through the native SDK, durable per-item budget reservations, result
            collection and provider-file cleanup. Local mock replies establish
            lifecycle behavior; they do not establish model quality or customer
            savings.
          </p>
          <button onClick={() => navigate("Provider batches")}>
            Inspect the native batch lifecycle
          </button>
          <p>
            Batch {String(demo.provider_batch_sample.batch_id).slice(0, 8)} ·
            Vendor files deleted:{" "}
            {demo.provider_batch_sample.vendor_files_deleted
              ? "yes"
              : "pending"}{" "}
            · Paid provider calls: zero.
          </p>
        </section>
      )}
      {demo.semantic_cache_sample && (
        <section aria-label="Qualified semantic cache sample">
          <h2>Refuse a tempting wrong cached answer</h2>
          <p>
            Inspect validation-only threshold selection, a separate confirmation
            set, and an activated encrypted catalog. These symbolic vector
            controls test the workflow; real embedding quality, customer
            outcomes and realized savings remain unverified.
          </p>
          <button onClick={() => navigate("Semantic caching")}>
            Inspect semantic cache qualification
          </button>
          <p>
            Selected threshold: {demo.semantic_cache_sample.selected_threshold}{" "}
            · Paid provider calls: zero.
          </p>
        </section>
      )}
      {demo.ticket_campaign_sample && (
        <section aria-label="Automatic ticket-routing task">
          <h3>Three labels. One automatic improvement.</h3>
          <p>
            The independent ticket router chooses billing, access or technical.
            Its 240 synthetic examples belong to 60 declared intent families;
            related variants stay together. A cheap constant answer must fail
            before the agent proposes a better instruction and confirms it.
          </p>
          <p>
            All provider replies and later outcomes are synthetic. Inspect the
            failed answers, cumulative campaign costs, application checks and
            rollback; these do not establish real-model accuracy or customer
            savings.
          </p>
          <div className="actions">
            <button
              onClick={() =>
                inspect(demo.ticket_campaign_sample.validation_run_id)
              }
            >
              Inspect ticket-routing failures
            </button>
            <button onClick={() => navigate("Campaigns")}>
              Follow the ticket-routing campaign
            </button>
          </div>
          <p>
            In Campaigns, inspect the row ending in{" "}
            {String(demo.ticket_campaign_sample.campaign_id).slice(0, 8)}.
            Encrypted cases require an explicit review purpose.
          </p>
        </section>
      )}
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Loading sample evidence…</p>}
      {detail && (
        <section aria-label="Sample experiment result">
          <h2 ref={resultHeading} tabIndex={-1}>
            Sample experiment result
          </h2>
          {evidence(detail)}
        </section>
      )}
      <details className="sample-method">
        <summary>
          How the sample works: source, observation, stages and rollback
        </summary>
        <ol className="sample-steps">
          <li>
            <h3>Find the AI call</h3>
            <p>
              The scanner reads the sample app without executing it. The model
              lives in <code>sample_app/app.py</code>.
            </p>
            <button onClick={() => navigate("Inventory")}>
              Inspect the scanned call
            </button>
          </li>
          <li>
            <h3>Observe the application</h3>
            <p>
              Native SDK replies are intercepted locally. Metadata travels
              through the authenticated HTTP collector into actual PostgreSQL.
              Fixture costs are modeled using the recorded catalog.
            </p>
            <button onClick={() => navigate("Costs")}>
              Inspect modeled usage
            </button>
          </li>
          <li>
            <h3>Compare quality and cost</h3>
            <p>
              The compact candidate preserves the sample labels. The unsafe
              candidate saves modeled cost but loses urgent priorities. Inspect
              the scorecard, segments and encrypted case outputs below.
            </p>
          </li>
          <li>
            <h3>Review a change and its rollback</h3>
            <p>
              The diff below changes the model in the sample source. It has not
              been applied. Passing synthetic sample gates is not verified
              customer quality or production approval.
            </p>
            <details>
              <summary>Review proposed source diff</summary>
              <pre tabIndex={0}>{demo.source_diff.diff}</pre>
            </details>
            <details>
              <summary>Review rollback diff</summary>
              <pre tabIndex={0}>{demo.source_diff.rollback_diff}</pre>
            </details>
          </li>
        </ol>
        <h3>Smoke → pilot → held-out test</h3>
        <p>
          Each stage has a separate frozen plan and one-run approval. Changed
          inputs or a failed previous stage block promotion.
        </p>
        <div className="table-scroll" tabIndex={0}>
          <table>
            <caption>Completed sample stages</caption>
            <thead>
              <tr>
                <th>Stage</th>
                <th>Cases</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {demo.stages.map((stage: Data) => (
                <tr key={stage.stage}>
                  <th scope="row">{stage.stage}</th>
                  <td>{stage.cases}</td>
                  <td>
                    <button onClick={() => inspect(stage.run_id)}>
                      Inspect {stage.stage}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          Next: read{" "}
          <a href="https://github.com/CutbackAI/cutback/blob/main/docs/application-onboarding.md">
            application onboarding
          </a>{" "}
          to start offline in your own repository. Keep application reports
          private.
        </p>
      </details>
    </section>
  );
}
