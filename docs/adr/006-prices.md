# ADR-006: Immutable offline pricing

Accepted. A small checksum-verified package snapshot records public numerical rate facts, URLs, checked/effective date, exact aliases and limitations. Start with two independently checked OpenAI text models rather than redistributing an unreviewed bulk catalog. Unknown model/category/time/tier returns null with a reason. Reasoning is not charged twice; cached input is partitioned. Catalog updates/overrides and invoice reconciliation remain future work. Historical stored valuations are never silently recomputed.

Repository text uses LF on every platform through `.gitattributes`; this keeps packaged catalog byte checksums stable across Windows/Linux checkouts.
