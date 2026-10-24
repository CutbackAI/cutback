# ADR-002: Customer-owned collector and storage

Accepted. SDK exports only to a configured customer endpoint; no default corporate address, license check or calls home. PostgreSQL behind the collector is the operational store, independently of application cloud. A local Compose profile proves the same HTTP boundary. No AWS/the pilot application schema assumption appears in the core. Public cloud deployment is neither required nor performed.
