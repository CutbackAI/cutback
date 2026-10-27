import test from "node:test";
import assert from "node:assert/strict";
import { decisionLabel, criterionLabel, experimentTitle } from "../src/evidence.ts";

test("technical or insufficient evidence never becomes a verified quality claim", () => {
  assert.match(decisionLabel({quality_gate_passed: true, evidence_tier: 1}), /semantic quality unknown/);
  assert.equal(decisionLabel({quality_gate_passed: true, evidence_tier: 4, insufficient_criteria:["safety"]}), "More evidence required");
  assert.equal(criterionLabel({sample_gate_passed: true, evidence_sufficient: false, passed:false}), "More samples or groups required");
  assert.equal(criterionLabel({sample_gate_passed:true, evidence_sufficient:true, regression_passed:false, passed:false}), "Fail");
  assert.equal(experimentTitle({name:"Support desk comparison"}), "Support desk comparison");
});
