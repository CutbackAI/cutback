import test from "node:test";
import assert from "node:assert/strict";
import {
  decisionLabel,
  criterionLabel,
  experimentTitle,
} from "../src/evidence.ts";

test("generated semantic rubrics remain proxy evidence after untouched confirmation", () => {
  const proxy = {
    evaluator_version: "deterministic-v14",
    quality_gate_passed: true,
    evidence_tier: 3,
    semantic_quality: "NOT VERIFIED",
    source_patch_eligible: false,
    criteria_binding: {
      version_sha256: "b".repeat(64),
      review_approval_id: "owned-epoch",
    },
    criteria_review_assurance: "owner_review_not_independent_judge_calibration",
    criteria_generation_cost: { billing_complete: true },
  };
  assert.match(decisionLabel(proxy), /untouched-test confirmation required/);
  assert.match(
    decisionLabel({
      ...proxy,
      criteria_confirmation_predecessor_run_id: "owned-validation",
    }),
    /semantic quality unverified/,
  );
  assert.match(
    decisionLabel({ ...proxy, quality_gate_passed: false }),
    /not eligible/,
  );
  assert.match(decisionLabel({ ...proxy, evidence_tier: 4 }), /incomplete/);
});

test("reviewed criteria distinguish validation from confirmation without a calibration claim", () => {
  const reviewed = {
    evaluator_version: "deterministic-v13",
    quality_gate_passed: true,
    evidence_tier: 4,
    status: "VERIFIED_ON_HELDOUT_DATASET",
    criteria_binding: {
      version_sha256: "a".repeat(64),
      review_approval_id: "owned-epoch",
    },
    criteria_review_assurance: "owner_review_not_independent_judge_calibration",
    criteria_generation_cost: { billing_complete: true },
  };
  assert.match(decisionLabel(reviewed), /untouched-test confirmation required/);
  assert.match(
    decisionLabel({
      ...reviewed,
      criteria_confirmation_predecessor_run_id: "owned-validation",
    }),
    /against reviewed checks/,
  );
  assert.match(
    decisionLabel({
      ...reviewed,
      criteria_generation_cost: { billing_complete: false },
    }),
    /new comparison/,
  );
  assert.match(
    decisionLabel({ ...reviewed, evidence_tier: 1 }),
    /semantic quality unknown/,
  );
});

test("technical or insufficient evidence never becomes a verified quality claim", () => {
  assert.match(
    decisionLabel({
      evaluator_version: "deterministic-v3",
      quality_gate_passed: true,
      evidence_tier: 1,
    }),
    /semantic quality unknown/,
  );
  assert.equal(
    decisionLabel({
      evaluator_version: "deterministic-v3",
      quality_gate_passed: true,
      evidence_tier: 4,
      insufficient_criteria: ["safety"],
    }),
    "More evidence required",
  );
  assert.equal(
    criterionLabel({
      sample_gate_passed: true,
      evidence_sufficient: false,
      passed: false,
    }),
    "More samples or groups required",
  );
  assert.equal(
    criterionLabel({
      sample_gate_passed: true,
      evidence_sufficient: true,
      regression_passed: false,
      passed: false,
    }),
    "Fail",
  );
  assert.equal(
    experimentTitle({ name: "Support desk comparison" }),
    "Support desk comparison",
  );
});

test("old or unknown evaluator evidence is not relabeled as newly validated", () => {
  assert.equal(
    decisionLabel({
      quality_gate_passed: true,
      evidence_tier: 4,
      status: "VERIFIED_ON_HELDOUT_DATASET",
      evaluator_version: "deterministic-v2",
    }),
    "Evaluation version requires a new comparison",
  );
  assert.equal(
    criterionLabel({ passed: true, value: 1 }),
    "Legacy check; evidence limits unknown",
  );
});
