export type Evidence = Record<string, any>;

export function decisionLabel(item: Evidence): string {
  if (
    !item.failed_criteria?.length &&
    (item.insufficient_criteria?.length ||
      item.decision_reason === "primary_metric_evidence_insufficient")
  )
    return "More evidence required";
  if (!item.quality_gate_passed) return "Not eligible";
  const tier = item.evidence_tier ?? 0;
  if (tier <= 1) return "Technical checks passed; semantic quality unknown";
  if (tier === 2) return "Reference-aligned; independent quality unknown";
  if (tier === 3) return "Judge-assessed; not independently verified";
  if (item.status === "VERIFIED_ON_HELDOUT_DATASET")
    return "Validated on this held-out dataset";
  return "Passes sample gates";
}

export function experimentTitle(spec: Evidence = {}): string {
  if (spec.name) return spec.name;
  const candidates = spec.candidates
    ?.map((item: Evidence) => item.key)
    .join(", ");
  return (
    [spec.workflow, spec.baseline?.model, candidates ? `vs ${candidates}` : ""]
      .filter(Boolean)
      .join(" · ") || "Experiment awaiting details"
  );
}

export function criterionLabel(item: Evidence): string {
  if (item.sample_gate_passed === false) return "Fail";
  if (item.evidence_sufficient === false)
    return "More samples or groups required";
  return item.passed ? "Pass" : "Fail";
}
