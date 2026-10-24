# ADR-013: One distribution, independently bounded modules

Accepted refinement of the proposed monorepo. Use `src/cutback/{core,scanner,pricing,instrumentation,server}` in one wheel, plus optional dependency extras. This delivers one install without early inter-package release churn. Modules retain scanner/event/pricing/repository contracts and can be split later. The collector image installs no provider SDK, and the scanner wheel smoke test installs no server/SDK dependencies. Tests/infra/examples/docs remain top-level. No architectural subsystem is replaced by a toy service.
