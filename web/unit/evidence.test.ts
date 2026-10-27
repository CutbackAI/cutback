import test from "node:test";
import assert from "node:assert/strict";
import {
  decisionLabel,
  criterionLabel,
  experimentTitle,
} from "../src/evidence.ts";

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
