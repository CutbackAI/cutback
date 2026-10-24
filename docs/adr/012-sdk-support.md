# ADR-012: Pinned SDK surfaces

OpenAI 2.29.0 Responses/Chat, legacy 1.55.3 Chat, and Anthropic 2.3.0 Messages have version-specific synthetic compatibility tests. Native sync/async/stream values/errors are preserved. Lower-level attempt hooks link distinct retry attempts to logical requests without reading response text by default. Untested versions/wrappers/helper APIs are refused or unsupported. Real provider/serverless acceptance remains a separate approved gate.
