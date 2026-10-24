# ADR 016: optional product modules preserve the independent foundation

Status: implemented for dev2.

Keep scanner parsing independent from runtime, server, identity, billing and training imports. Node instrumentation is a separate native SDK package. OIDC identities acquire permissions from database membership/grants, never arbitrary role claims. Browser access tokens stay in memory and PKCE callbacks carry one-use state.

Routing, pilots, schedules and training use frozen plans with conservative pre-call/compute reservations. Uncertain crashed operations require a new approval; training loss is diagnostic. Approved raw datasets/configs/adapters/cache remain in the separate encrypted store. Release migrations are additive and immutable. Organization-scoped project names use 0004; unsafe downgrade refuses conflicting names.

Helm/Terraform consume approved existing infrastructure and external secret references with separate migration/collector/inference/training privileges. The optional billing connector does not gate OSS and is disabled until configured. Synthetic contract acceptance never becomes live-provider, customer-quality, GPU or production evidence.
