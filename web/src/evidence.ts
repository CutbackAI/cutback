export type Evidence = Record<string, any>;

export function decisionLabel(item: Evidence): string {
  const reviewed =
    ["deterministic-v13", "deterministic-v14"].includes(
      item.evaluator_version,
    ) &&
    /^[a-f0-9]{64}$/.test(item.criteria_binding?.version_sha256 ?? "") &&
    item.criteria_binding?.review_approval_id &&
    item.criteria_review_assurance ===
      "owner_review_not_independent_judge_calibration" &&
    item.criteria_generation_cost?.billing_complete === true;
  if (item.evaluator_version !== "deterministic-v3" && !reviewed)
    return "Evaluation version requires a new comparison";
  if (item.decision_reason === "test_group_lineage_not_fresh")
    return "Test groups already exposed or unknown; new confirmation required";
  if (item.evaluator_version === "deterministic-v14") {
    if (
      item.evidence_tier !== 3 ||
      item.semantic_quality !== "NOT VERIFIED" ||
      item.source_patch_eligible !== false
    )
      return "Reviewed semantic proxy evidence is incomplete";
    if (!item.quality_gate_passed) return "Proxy comparison not eligible";
    return item.criteria_confirmation_predecessor_run_id
      ? "Reviewed proxy confirmed on untouched groups; semantic quality unverified"
      : "Reviewed proxy passes validation; untouched-test confirmation required";
  }
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
  if (reviewed && !item.criteria_confirmation_predecessor_run_id)
    return "Reviewed checks pass validation; untouched-test confirmation required";
  if (reviewed && item.status === "VERIFIED_ON_HELDOUT_DATASET")
    return "Validated against reviewed checks on this held-out dataset";
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
  if (item.evidence_sufficient === undefined)
    return "Legacy check; evidence limits unknown";
  if (item.sample_gate_passed === false) return "Fail";
  if (item.evidence_sufficient === false)
    return "More samples or groups required";
  return item.passed ? "Pass" : "Fail";
}
