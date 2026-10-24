# ADR-009: Explicit consequential actions

Accepted. Development/test dependencies and local Docker services are within implementation scope. No production deployment, cloud provisioning, paid API request or raw content capture is authorized by a scan or import. Instrumentation commands default to a diff and require explicit `--apply` to write local application source. No model or traffic change is automatic. Live staging/provider acceptance requires vendor/data/spend/deployment approval; no such call has been made.
