# ADR-001: Deterministic Python AST discovery

Accepted. Python 3.11 is the floor. Parse bounded source without imports/eval/LLMs. Retain source locations and redacted symbolic model expressions; dynamic facts stay unresolved. Direct SDK aliases and local cross-file bindings are supported conservatively. No universal call graph or recall claim. Cross-language parsers and richer detectors are later adapters. See scanner tests and limitations.md.
