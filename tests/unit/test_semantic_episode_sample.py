from cutback.demo_agents import demonstrate, rag_case, specification, synthetic_executor
from cutback.evals.experiments import ExperimentPlan, plan_summary, run


def test_packaged_semantic_sample_includes_judge_overhead_and_keeps_wrong_answer_failed():
    report = demonstrate(semantic=True)
    assert report["status"] == "COMPLETED" and report["requests"] == 12
    assert report["billing_complete"] and report["recommendations"] == []
    assert [m["quality"]["value"] for m in report["models"]] == [1, 0]
    assert all(m["semantic_judging"]["judge_requests"] == 2 for m in report["models"])
    assert all(m["target_requests"] == 4 for m in report["models"])


def test_actual_native_retrieval_can_be_assessed_by_three_separately_scoped_proxy_criteria():
    case = rag_case()
    dataset = {
        "immutable_hash": "a" * 64,
        "items": [{"case": case, "split": "test", "group_hash": "owned-rag"}],
    }
    raw = specification(dataset["immutable_hash"], semantic=True).model_dump(mode="json")
    raw["episode_execution"]["semantic_judging"]["criteria"] = [
        {"key": kind, "kind": kind, "judge_rubric": "Assess this declared task constraint."}
        for kind in ("answer_correctness", "retrieval_groundedness", "conversation_constraints")
    ]
    plan = ExperimentPlan.model_validate(raw)
    executor = synthetic_executor()
    retained = []
    try:
        raw["retain_case_outputs"] = True
        plan = ExperimentPlan.model_validate(raw)
        report = run(
            plan,
            dataset,
            executor,
            approval_hash=plan_summary(plan, dataset)["plan_hash"],
            record_case=retained.append,
        )
        assert report["status"] == "COMPLETED" and report["requests"] == 6
        assert [m["quality"]["value"] for m in report["models"]] == [1, 0]
        assert all(len(m["semantic_judging"]["criteria"]) == 3 for m in report["models"])
        assert all(
            c["verified_quote_count"] == 1
            for r in retained
            for c in r["episode_assessment"]["semantic_judging"]["checks"]
        )
    finally:
        executor.close()
