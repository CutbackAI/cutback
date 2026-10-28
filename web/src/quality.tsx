import React, { useEffect, useRef, useState } from "react";
import {
  type API,
  type Data,
  Check,
  Field,
  Form,
  Select,
  explain,
  json,
  money,
} from "./ui";
import { Investigation } from "./traces";

export function Quality({
  api,
  base,
  admin,
}: {
  api: API;
  base: string;
  admin: boolean;
}) {
  const [page, setPage] = useState<Data | null>(null);
  const [policies, setPolicies] = useState<Data[]>([]);
  const [prompts, setPrompts] = useState<Data[]>([]);
  const [versions, setVersions] = useState<Data[]>([]);
  const [versionCursor, setVersionCursor] = useState<number | null>(null);
  const [prompt, setPrompt] = useState("");
  const [kind, setKind] = useState("nonempty_output");
  const [provider, setProvider] = useState("openai");
  const [selected, setSelected] = useState<Data | null>(null);
  const [receipts, setReceipts] = useState<Data | null>(null);
  const [comparison, setComparison] = useState<Data | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [trace, setTrace] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const requestId = useRef(crypto.randomUUID());
  async function load() {
    const [rules, p, registry] = await Promise.all([
      api(base + "/quality-rules"),
      admin ? api(base + "/capture-policies") : Promise.resolve([]),
      admin ? api(base + "/prompts?limit=100") : Promise.resolve({ items: [] }),
    ]);
    setPage(rules);
    setPolicies(p);
    setPrompts(registry.items);
  }
  useEffect(() => {
    let active = true;
    api(base + "/quality-rules")
      .then((r) => {
        if (active) setPage(r);
      })
      .catch((e) => {
        if (active) setError(explain(e));
      });
    if (admin)
      Promise.all([
        api(base + "/capture-policies"),
        api(base + "/prompts?limit=100"),
      ])
        .then(([p, r]) => {
          if (active) {
            setPolicies(p);
            setPrompts(r.items);
          }
        })
        .catch((e) => {
          if (active) setError(explain(e));
        });
    return () => {
      active = false;
      generation.current++;
    };
  }, [api, base, admin]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(explain(e));
    } finally {
      setBusy(false);
    }
  }
  async function inspect(rule: Data) {
    const token = ++generation.current;
    setSelected(rule);
    setReceipts(null);
    setComparison(null);
    setContent(null);
    setTrace("");
    const result = await api(base + `/quality-rules/${rule.id}/evaluations`);
    if (generation.current === token) setReceipts(result);
  }
  return (
    <section aria-label="Authorized online content quality">
      <h2>Online content quality</h2>
      <p>
        Check captured outputs against a technical contract or an approved model
        judge. Judge assessments are estimates, not independent evidence of task
        success. Content use requires an active encrypted capture policy and
        separate rule approval.
      </p>
      <p className="hint">
        Collector quality flag:{" "}
        {page?.collector_quality_execution_setting
          ? "enabled on this collector"
          : "disabled on this collector"}
        . Separate worker execution state is unknown to this collector. Saving a
        rule does not enable worker execution. Population coverage is unknown.
      </p>
      {error && (
        <p role="alert">
          {error}{" "}
          <button disabled={busy} onClick={() => action(load)}>
            Retry quality data
          </button>
        </p>
      )}
      {!page && !error && <p role="status">Loading quality rules…</p>}
      {page?.items.length === 0 && <p>No content quality rules approved.</p>}
      {page?.items.map((r: Data) => (
        <div className="record" key={r.id}>
          <h3>
            {r.key} · v{r.revision}
          </h3>
          <p>
            {r.specification.evaluator.replaceAll("_", " ")} ·{" "}
            {r.enabled ? "enabled" : "disabled"} ·{r.specification.environment}{" "}
            / {r.specification.workflow}
          </p>
          <p>
            Requests reserved: {r.requests_used} /{" "}
            {r.specification.max_requests}; reserved upper cost{" "}
            {money(r.reserved_upper_usd)} /{" "}
            {money(r.specification.max_total_usd)}; known completed spend{" "}
            {money(r.known_spent_usd)}. Interrupted charged requests may have
            unknown spend. Requests with known cost: {r.known_cost_requests};
            unknown or pending cost: {r.unknown_or_pending_cost_requests}.
          </p>
          <button disabled={busy} onClick={() => action(() => inspect(r))}>
            Inspect quality rule: {r.key}
          </button>
          {admin && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await json(api, base + `/quality-rules/${r.id}/switch`, {
                    enabled: !r.enabled,
                    expected_enabled: r.enabled,
                    expected_revision: r.revision,
                  });
                  await load();
                  setSelected(null);
                  setReceipts(null);
                  setContent(null);
                })
              }
            >
              {r.enabled ? "Pause" : "Resume"} quality rule: {r.key}
            </button>
          )}
        </div>
      ))}
      {page?.next_cursor && (
        <button
          disabled={busy}
          onClick={() =>
            action(async () => {
              const next = await api(
                base + "/quality-rules?after=" + page.next_cursor,
              );
              setPage({ ...next, items: [...page.items, ...next.items] });
            })
          }
        >
          Load more quality rules
        </button>
      )}
      {selected && (
        <section aria-label="Quality rule investigation">
          <h3>{selected.key}: retained evaluation receipts</h3>
          <p>
            Only selected retained sources are represented. Missing and
            evaluator errors are not application failures.
          </p>
          {!receipts && <p role="status">Loading evaluation receipts…</p>}
          {receipts?.items.length === 0 && <p>No retained quality receipts.</p>}
          {receipts?.items.map((r: Data) => (
            <div className="record" key={r.id}>
              <p>
                <strong>{r.state}</strong> · {r.result.code || "pending"} ·{" "}
                {r.result.evidence || "not yet assessed"}
              </p>
              <p>
                Known spend: {money(r.known_spent_usd)}; reserved upper cost:{" "}
                {money(r.reserved_upper_usd)}.
              </p>
              {r.trace_record_id && (
                <button onClick={() => setTrace(r.trace_record_id)}>
                  Investigate source trace
                </button>
              )}
              {admin && r.output_retained && (
                <Form
                  title={"Read retained judge output " + r.id}
                  submit="Read encrypted judge output"
                  onSubmit={async (f) => {
                    const token = generation.current;
                    const value = await json(
                      api,
                      base + `/quality-evaluations/${r.id}/output`,
                      { purpose: f.purpose },
                    );
                    if (token === generation.current)
                      setContent(String(value.output));
                  }}
                >
                  <Field
                    label="Investigation purpose"
                    name="purpose"
                    required
                    maxLength={200}
                  />
                </Form>
              )}
            </div>
          ))}
          {receipts?.next_cursor && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const token = generation.current;
                  const next = await api(
                    base +
                      `/quality-rules/${selected.id}/evaluations?after=` +
                      receipts.next_cursor,
                  );
                  if (token === generation.current)
                    setReceipts({
                      ...next,
                      items: [...receipts.items, ...next.items],
                    });
                })
              }
            >
              Load more quality receipts
            </button>
          )}
          {content !== null && (
            <div>
              <button onClick={() => setContent(null)}>
                Forget judge output
              </button>
              <pre>{content}</pre>
            </div>
          )}
          {trace && (
            <Investigation
              key={trace}
              api={api}
              base={base}
              id={trace}
              allowContent={admin}
            />
          )}
          <Form
            title="Compare observed quality cohorts"
            submit="Compare retained cohorts"
            onSubmit={async (f) => {
              const token = generation.current;
              const body = Object.fromEntries(
                Object.entries(f).map(([k, v]) => [
                  k,
                  k === "group_by" ? v : new Date(String(v)).toISOString(),
                ]),
              );
              const result = await json(
                api,
                base + `/quality-rules/${selected.id}/compare`,
                body,
              );
              if (token === generation.current) setComparison(result);
            }}
          >
            <Field
              label="Baseline start"
              name="baseline_start"
              type="datetime-local"
              required
            />
            <Field
              label="Baseline end"
              name="baseline_end"
              type="datetime-local"
              required
            />
            <Field
              label="Current start"
              name="current_start"
              type="datetime-local"
              required
            />
            <Field
              label="Current end"
              name="current_end"
              type="datetime-local"
              required
            />
            <Select label="Compare categories" name="group_by">
              <option value="release">Application release</option>
              <option value="service">Service</option>
              <option value="input_size_bucket">
                Authorized input size bucket
              </option>
            </Select>
          </Form>
          {comparison && (
            <section aria-label="Observed cohort comparison">
              <h3>Observed change</h3>
              <p>
                Baseline: {comparison.baseline.retained_selected} retained
                sources; current: {comparison.current.retained_selected}.
              </p>
              <p>
                Failure fraction change among decided receipts:{" "}
                {comparison.failure_fraction_delta ?? "Unknown"}. Known category
                total variation:{" "}
                {comparison.known_category_total_variation ?? "Unknown"}.
              </p>
              <p>
                Descriptive observation. Significance, causation and population
                coverage are unknown. Deleted receipts are not reconstructed.
              </p>
              <details>
                <summary>Cohort counts and missing categories</summary>
                <pre>{JSON.stringify(comparison, null, 2)}</pre>
              </details>
            </section>
          )}
        </section>
      )}
      {admin && (
        <Form
          title="Approve online content quality rule"
          submit="Approve bounded quality rule"
          description="Use a unique rule key. Model judging requires a pinned prompt with string input/output variables and the documented judge schema. Prompt selectors show the first 100 registry records; older versions can be loaded."
          onSubmit={async (f) => {
            if (f.consent !== "on")
              throw new Error("EXPLICIT_ONLINE_CONTENT_USE_APPROVAL_REQUIRED");
            const now = Date.now();
            await json(api, base + "/quality-rules", {
              version_id: requestId.current,
              key: f.key,
              expected_revision: 0,
              environment: f.environment,
              workflow: f.workflow,
              source_policy_id: f.policy,
              evaluator: kind,
              content_use_authorized: true,
              record_input_features: f.features === "on",
              retain_judge_output: kind === "model_judge" && f.retain === "on",
              ...(kind !== "nonempty_output"
                ? { prompt_version_id: f.version }
                : {}),
              ...(kind === "model_judge"
                ? {
                    judge: {
                      provider,
                      model: f.model,
                      surface:
                        provider === "anthropic"
                          ? "messages.create"
                          : "responses.create",
                      max_input_tokens: Number(f.input_cap),
                      max_output_tokens: Number(f.output_cap),
                    },
                    max_requests: Number(f.requests),
                    max_total_usd: String(f.usd),
                  }
                : {}),
              starts_at: new Date(now).toISOString(),
              expires_at: new Date(
                now + Number(f.hours) * 3600000,
              ).toISOString(),
              max_evaluations: Number(f.evaluations),
              sample_basis_points: Number(f.sample),
              approval_ref: f.approval,
            });
            requestId.current = crypto.randomUUID();
            await load();
            return "Quality rule approved. Execution remains under the operator’s worker setting.";
          }}
        >
          <Field label="Quality rule key" name="key" required maxLength={200} />
          <Field
            label="Quality environment"
            name="environment"
            defaultValue="staging"
            required
          />
          <Field label="Quality workflow" name="workflow" required />
          <Select label="Source capture policy" name="policy" required>
            <option value="">Choose an active policy</option>
            {policies
              .filter((p) => p.enabled)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.policy.workflows.join(", ")} ·{" "}
                  {p.policy.environments.join(", ")} · {p.id}
                </option>
              ))}
          </Select>
          <Select
            label="Quality evaluator"
            name="kind"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="nonempty_output">Nonempty captured output</option>
            <option value="output_schema">Pinned JSON schema</option>
            <option value="model_judge">Approved model judge</option>
          </Select>
          {kind !== "nonempty_output" && (
            <>
              <Select
                label="Quality prompt"
                name="prompt"
                required
                value={prompt}
                onChange={(e) => {
                  const id = e.target.value;
                  setPrompt(id);
                  setVersions([]);
                  const token = ++generation.current;
                  if (id)
                    action(async () => {
                      const r = await api(base + `/prompts/${id}/versions`);
                      if (token === generation.current) {
                        setVersions(r.items);
                        setVersionCursor(r.next_cursor);
                      }
                    });
                }}
              >
                <option value="">Choose a prompt</option>
                {prompts.map((p) => (
                  <option value={p.id} key={p.id}>
                    {p.key}
                  </option>
                ))}
              </Select>
              <Select
                label="Pinned quality prompt version"
                name="version"
                required
              >
                <option value="">Choose an immutable version</option>
                {versions.map((v) => (
                  <option value={v.id} key={v.id}>
                    v{v.ordinal} · {v.id}
                  </option>
                ))}
              </Select>
              {versionCursor && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      const r = await api(
                        base +
                          `/prompts/${prompt}/versions?before=${versionCursor}`,
                      );
                      setVersions([...versions, ...r.items]);
                      setVersionCursor(r.next_cursor);
                    })
                  }
                >
                  Load older quality prompt versions
                </button>
              )}
            </>
          )}
          {kind === "model_judge" && (
            <>
              <Select
                label="Judge provider"
                name="provider"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
              >
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
              </Select>
              <Field
                key={provider}
                label="Qualified judge model"
                name="model"
                defaultValue={
                  provider === "openai" ? "gpt-4.1-mini" : "claude-haiku-4-5"
                }
                required
              />
              <Field
                label="Judge input token cap"
                name="input_cap"
                type="number"
                min={1}
                max={100000}
                defaultValue={8192}
                required
              />
              <Field
                label="Judge output token cap"
                name="output_cap"
                type="number"
                min={16}
                max={16384}
                defaultValue={256}
                required
              />
              <Field
                label="Total judge request cap"
                name="requests"
                type="number"
                min={1}
                max={10000}
                defaultValue={10}
                required
              />
              <Field
                label="Total judge USD cap"
                name="usd"
                type="number"
                min="0.000001"
                max="10000"
                step="0.000001"
                defaultValue="1"
                required
              />
              <Check
                label="Retain judge output encrypted under the same capture quota"
                name="retain"
              />
            </>
          )}
          <Field
            label="Maximum selected evaluations"
            name="evaluations"
            type="number"
            min={1}
            max={50000}
            defaultValue={100}
            required
          />
          <Field
            label="Sample basis points (10000 is all observed matches)"
            name="sample"
            type="number"
            min={1}
            max={10000}
            defaultValue={10000}
            required
          />
          <Field
            label="Rule duration in hours"
            name="hours"
            type="number"
            min={1}
            max={720}
            defaultValue={24}
            required
          />
          <Field
            label="Quality approval reference"
            name="approval"
            required
            maxLength={200}
          />
          <Check
            label="Authorize coarse input size features for observed drift comparisons"
            name="features"
          />
          <Check
            label="I authorize this scoped content use and the displayed request and dollar caps"
            name="consent"
            required
          />
        </Form>
      )}
    </section>
  );
}
