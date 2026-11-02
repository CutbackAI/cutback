"""No models or network. The full check adds independent negative cases."""

import sys


def check(workflow, scope):
    if workflow == "triage":
        from triage import classify

        assert classify(" INVOICE missing ") == "billing"
        if scope == "full":
            assert classify("hello") == "general"
    elif workflow == "escalation":
        from escalation import priority

        assert priority(" BLOCKED account ") == "urgent"
        if scope == "full":
            assert priority("hello") == "normal"
    elif workflow == "health":
        from health import status

        assert status() == "ready"
        if scope == "full":
            assert isinstance(status(), str)
    else:
        raise ValueError("UNKNOWN_SYNTHETIC_WORKFLOW")


if __name__ == "__main__":
    check(*sys.argv[1:])
