# ADR-003: Metadata-only, strict allowlist

Accepted. Default events cannot contain content, arbitrary attributes or raw provider objects. Unknown fields are forbidden. Requested content capture is rejected until a separate authorized encrypted/expiring payload store is implemented. Error messages and provider request identifiers are not exported. This intentionally limits replay/evaluation while protecting day-zero usage monitoring.
