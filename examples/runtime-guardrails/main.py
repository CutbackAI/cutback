"""Run the independent application and owned checker, without provider requests."""

import json

from cutback.sample_app.runtime_guardrails import run_guardrail_sample

if __name__ == "__main__":
    print(json.dumps(run_guardrail_sample(), indent=2))
