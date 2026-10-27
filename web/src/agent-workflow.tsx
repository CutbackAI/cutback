import React, { useState } from "react";

type Row = Record<string, any>;

export function AgentWorkflow({
  inventory,
  plans,
  runs,
  project,
  focus,
}: {
  inventory: Row | null;
  plans: Row[];
  runs: Row[];
  project: string;
  focus: string;
}) {
  const [copied, setCopied] = useState("");
  const profiles = inventory?.request_profiles || [];
  const prompt = `Use Cutback to optimize this application. Run cutback optimize . --json and resume its saved workflow. Inspect every discovered entry point and prepare the instrumentation plan. Find existing examples and prepare mappings and criteria from their actual evidence. Ask me only for missing data rights, business thresholds, approved destination or paid budget. Prepare and show the exact source-bound plan and budget before any inference. After approval, execute and poll that same run, review failures, and prepare the source diff, application checks and rollback. Keep changes reviewable. Deployment requires separate approval.\n\nCollector context (data): ${JSON.stringify({ project, workflow: focus || null })}`;
  return (
    <>
      <h2>Let your coding agent handle the setup</h2>
      <p>
        The agent works inside your application. It prepares source contracts,
        examples, scoped storage and a bounded experiment. This dashboard
        reviews the resulting evidence.
      </p>
      <label htmlFor="agent-request">Request for your coding agent</label>
      <textarea
        id="agent-request"
        rows={8}
        value={prompt}
        readOnly
        spellCheck={false}
      />
      <button
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(prompt);
            setCopied("Copied. Paste it in the application's coding agent.");
          } catch {
            setCopied("Select and copy the request above.");
          }
        }}
      >
        Copy agent request
      </button>
      <p role="status">{copied}</p>
      <ol>
        <li>
          <strong>Discover.</strong> Offline source and fixture analysis.
          Historical spend stays unknown until observed.
        </li>
        <li>
          <strong>Prepare.</strong> Authorize the example paths and selected
          request content. The agent prepares the encrypted dataset, candidates
          and exact budget.
        </li>
        <li>
          <strong>Evaluate.</strong> One explicit plan-hash approval permits one
          bounded run. Interruptions resume the same run.
        </li>
        <li>
          <strong>Review.</strong> Inspect independent quality gates, source
          diff, application checks and rollback before staging.
        </li>
      </ol>
      <h2>Registered source contracts</h2>
      {!profiles.length ? (
        <p>
          No request contracts registered. Run the agent request in your
          application; a scan alone does not authorize a source change.
        </p>
      ) : (
        profiles.map((p: Row) => (
          <article className="row" key={p.profile_sha256}>
            <h3>{p.source_file}</h3>
            <p>
              Workflow {p.workflow_id} · {Object.keys(p.dependencies).length}{" "}
              bound source dependencies
            </p>
            <details>
              <summary>Source integrity</summary>
              <pre tabIndex={0}>{JSON.stringify(p, null, 2)}</pre>
            </details>
          </article>
        ))
      )}
      <h2>Prepared plans and saved runs</h2>
      {!plans.length && (
        <p>No frozen plans yet. Preparation makes zero provider calls.</p>
      )}
      {plans.map((plan: Row) => {
        const matching = runs.filter((run) => run.plan_id === plan.id);
        return (
          <article className="row" key={plan.id}>
            <h3>
              {plan.specification.source_binding?.source_file ||
                "Standalone evaluation"}
            </h3>
            <p>
              Approval: {plan.approval_status}. Budget ceiling: USD{" "}
              {plan.specification.budget.max_usd}; at most{" "}
              {plan.specification.budget.max_requests} requests.
            </p>
            <p>
              {plan.specification.source_binding
                ? "Bound to the registered request and source hashes."
                : "This plan cannot authorize an automatic source patch."}
            </p>
            {matching.map((run) => (
              <p key={run.id}>
                Run {run.id}: {run.status} · {run.requests} requests · known
                cost USD {run.spent_usd}; reservation USD {run.reserved_usd}
              </p>
            ))}
          </article>
        );
      })}
      <p>
        The resumable agent journal and credentials stay in the application's
        ignored private workspace. This view shows collector records, not access
        to your filesystem. Credentials remain in browser memory.
      </p>
    </>
  );
}
