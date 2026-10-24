# ADR-004: Pinned resource wrappers, OTel context, JSON 1.0

Accepted for the first slice. Carefully bounded OpenAI resource wrappers preserve native results/streams/errors and read OTel parent context. They do not initialize or replace a global OTel provider. The exporter uses versioned Cutback JSON, not OTLP; no OTLP compatibility is claimed. Existing wrapper conflicts are rejected explicitly. Full GenAI instrumentor/OTLP translation remains a separately tested adapter. This follows the handoff's permitted provider-wrapper path and avoids accidental content-bearing OTel attributes.
