"""Independent synthetic tool and retrieval conversations; no provider network I/O."""

import json

from cutback.demo_agents import demonstrate


def main():
    report = demonstrate(reactive=True)
    print(json.dumps(report, indent=2))
    if report["status"] != "COMPLETED" or not report["billing_complete"]:
        raise SystemExit("Synthetic reactive example did not complete")


if __name__ == "__main__":
    main()
