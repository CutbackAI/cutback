# Self-hosted infrastructure references

Cutback uses customer-owned PostgreSQL and an optional customer-owned encrypted object store. Nothing here provisions a cloud account, database, bucket, certificate or paid GPU. Applying a Helm/Terraform deployment to staging or production requires the owner's explicit environment and spend authorization. Local Compose and rendering/validation are independent of that decision.

## Compose

The [local Compose stack](compose/docker-compose.yml) binds the collector and PostgreSQL to loopback, uses ignored generated secrets, and starts maintenance with inference/content capture off. [The deployment guide](../docs/deployment.md) covers the TLS proxy reference, backup/restore and lifecycle. The worker does not receive the bootstrap control credential. The server image contains the supported native provider, identity, storage and billing extras; scanner-only pip installs remain independent.

## Helm

The chart is at `infra/helm/cutback`. Native acceptance uses Helm **4.3.1** and strict Kubernetes **1.35** OpenAPI schemas. It renders separate collector/worker deployments, an advisory-lock-protected migration Job, private ClusterIP service, network policies, nonroot read-only containers, scoped secret mounts, resource requests/limits, database/schema-aware probes, disruption budget and optional HPA. No PVC, database or secret value is created implicitly. A policy-enforcing CNI, resource quotas/Pod Security admission and actual network/TLS acceptance are customer prerequisites.

Before installing, prepare:

1. An existing namespace and a reviewed immutable image registry digest. Build the reviewed `infra/compose/Dockerfile` into your approved registry, or use a digest explicitly published with a release. Rendering with a placeholder digest does not prove an image exists.
2. A runtime Secret named by `secrets.runtime`, containing `database_url` and `admin_token`. Use an application database role with necessary DML/schema access. Keep all values outside Git and Terraform values/state. PostgreSQL TLS/CA verification belongs in the reviewed database URL; optional `database.tlsCAKey` mounts a private CA at `/run/cutback/database_ca.pem`.
3. A **separate** migration Secret containing `database_url` with migration privileges. Its optional CA mounts at `/run/migration/database_ca.pem`. The migration hook receives no admin/provider credentials. Application pods skip DDL and only become ready at the expected schema revision. Back up before upgrades. Do not automatically roll old binaries back across an incompatible schema; restore into a separate database using the reviewed recovery procedure.
4. Explicit network peers. Defaults deny workload ingress and all egress except scoped cluster DNS. `network.databasePeers` allows only the private PostgreSQL address/namespace/pod peers on TCP 5432. Authorize application/ingress controller peers through `network.ingressPeers`; add narrowly scoped identity/object-store/provider egress separately. Standard NetworkPolicy is IP/selector based; changing provider IPs require an approved egress gateway or updated rules.
5. For remote access, an existing TLS ingress class/certificate, `https` public URL, and exact trusted proxy IPs/restricted CIDRs. Wildcard and `/0` proxy trust are refused. There is no public LoadBalancer by default. Do not expose the plaintext backend outside its controlled namespace.

Create a nonsecret reviewed values file and render it first:

```sh
helm lint infra/helm/cutback --values reviewed-values.yaml
helm template reviewed infra/helm/cutback --values reviewed-values.yaml
```

`tests/contract/test_helm.py` validates actual rendered defaults and opt-ins against Kubernetes schemas, refuses incomplete unsafe combinations, and checks credential separation. Running `helm upgrade --install` is a separate authorized deployment step. Migration networking is a pre-install hook policy so the migration Job can reach the database in a namespace that already denies egress; Helm retains that narrow policy until the next hook/namespace cleanup. Include it in an explicit release-uninstall cleanup procedure.

## Optional services

Supplied-vector semantic caching has separate disabled defaults: `semanticCache.enabled` authorizes its API and `execution.semanticCache` enables bounded local finalization/qualification. Both require approved encrypted payload storage; local qualification needs no model credential. `execution.batches` additionally requires an enabled worker and explicitly selected OpenAI key, mounted only on the worker. Network egress remains explicitly configured. Compose exposes equivalent disabled `CUTBACK_SEMANTIC_CACHE_ENABLED`, `CUTBACK_SEMANTIC_CACHE_EXECUTION` and `CUTBACK_BATCH_EXECUTION` flags; configure the approved store and separate worker-only provider references in a private overlay before opting in. The TLS Compose worker does not mount the installation administrator credential.

Payload storage remains disabled until approved. Enabling it requires `payload_key` in the runtime Secret and a permitted S3 bucket/workload identity, or an existing shared file PVC. File storage across collector/worker/training pods requires suitable shared access and ownership; the chart does not infer that an RWO claim is safe for a replicated deployment. Configure object version/backup expiry, encryption-key recovery and orphan reconciliation separately.

Experiment execution and schedules are off. Enable them only with frozen data/vendor/budget approvals. Only selected provider credentials from the separate experiment Secret mount on the ordinary worker. The collector gets none. Native SDK retries remain off for billable experiment calls.

Identity uses the OIDC/JWKS/audience/client configuration described in [identity and teams](../docs/identity-and-teams.md); browser tokens and PKCE verifiers stay in memory. Optional billing mounts Stripe restricted/webhook secrets on the collector only. It remains disabled, self-hosting is ungated, and live charges require both server enablement and separate launch approval. Choosing actual commercial prices is an owner decision; placeholder price IDs are never created.

Training uses a **separate**, optional single worker with read-only registered model PVC, isolated runtime image, bounded scratch/resources, encrypted artifact storage and explicit compute rates. It gets neither admin nor provider keys. The ordinary worker explicitly disables training, preventing it from consuming jobs without the isolated runtime. Build `infra/training/Dockerfile` using a reviewed collector base image; its pinned CPU runtime has no model download or hosted trainer. CUDA needs an owner-approved compatible image, one assigned device and appropriate resource limits; CPU acceptance does not validate GPUs. See [local training](../docs/local-training.md).

## Terraform

`infra/terraform/kubernetes` wraps the same chart with the signed, locked HashiCorp Helm provider **3.3.0**. Native checks use Terraform **1.17.0**. It requires an exact context, existing namespace, reviewed image digest and external secret names. It does not create a namespace, put credentials in values, provision a cloud resource or automatically roll binaries back after migration failure.

```sh
terraform -chdir=infra/terraform/kubernetes init -backend=false
terraform -chdir=infra/terraform/kubernetes validate
terraform -chdir=infra/terraform/kubernetes test
```

Tests use a mock Helm provider and do not contact a cluster. `terraform.tfvars.example` contains documentation-only placeholders. Never run `apply` against an inferred context. Real staging ingress, workload identity, object lifecycle, network-policy enforcement, backup restoration and workload capacity need acceptance on the chosen customer environment.
