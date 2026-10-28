import { type Data } from "./ui";

export function NativeContracts({ plan, planId, context = "plan" }: {
  plan: Data; planId: string; context?: "plan" | "campaign";
}) {
  const models: Data[] = [plan.baseline, ...(plan.candidates || []), plan.judge].filter(Boolean);
  const targets = models.flatMap((model) => model.provider === "router"
    ? (model.routes || []).map((route: Data) => ({ role: model.key, model: route.target }))
    : [{ role: model.key, model }]).filter(({ model }) => model.native_contract);
  if (!targets.length) return null;
  return <section aria-label={`Native execution contracts for ${context} ${planId}`}>
    <h4>Native model and price contracts</h4>
    <p>These settings and exact price references are part of the approval hash.
      Missing usage or an unqualified served tier stops further calls.</p>
    {targets.map(({ role, model }, index) => {
      const reference = model.native_contract;
      return <div key={`${role}/${model.key}/${index}`}>
        <p><strong>{role} · {model.provider} / {model.model}</strong><br />
          Responses text · Reasoning: {reference.reasoning_effort} · Tier: {reference.service_tier}
        </p>
        {reference.service_tier === "auto" && <p className="hint">
          Preserves the application project tier. The reservation includes the Fast premium;
          only qualified actual pricing can become a known cost.
        </p>}
        <p>Price snapshot: <code>{reference.price_snapshot_id}</code><br />
          SHA-256: <code style={{ overflowWrap: "anywhere" }}>{reference.price_sha256}</code>
        </p>
      </div>;
    })}
  </section>;
}
