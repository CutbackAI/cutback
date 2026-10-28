"""Render and inspect actual Helm manifests, without contacting any cluster."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + "a" * 64


def test_metadata_online_and_notifications_mount_only_dedicated_signing_refs(tmp_path):
    path = tmp_path / "notifications.local.yaml"
    values = {
        "execution": {"online": True},
        "notifications": {
            "enabled": True,
            "signingKeys": [
                {"name": "CUTBACK_WEBHOOK_SECRET_SYNTHETIC", "key": "synthetic_signing"}
            ],
        },
    }
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    rows = render("--values", str(path))
    for row in rows:
        if row["kind"] != "Deployment":
            continue
        container = row["spec"]["template"]["spec"]["containers"][0]
        env = {e["name"]: e for e in container["env"]}
        assert env["CUTBACK_WEBHOOK_DESTINATIONS"]["valueFrom"]["secretKeyRef"] == {
            "name": "cutback-notifications",
            "key": "destinations",
        }
        if container["name"] == "worker":
            assert env["CUTBACK_WEBHOOK_SECRET_SYNTHETIC"]["valueFrom"]["secretKeyRef"] == {
                "name": "cutback-notifications",
                "key": "synthetic_signing",
            }
            assert "admin_token" not in str(container)
        else:
            assert "CUTBACK_WEBHOOK_SECRET_SYNTHETIC" not in env
    render("--values", str(path), "--set", "secrets.notifications=cutback-runtime", expect_ok=False)
    render(
        "--values",
        str(path),
        "--set",
        "notifications.signingKeys[0].name=CUTBACK_ADMIN_TOKEN",
        expect_ok=False,
    )


def test_quality_only_execution_mounts_selected_native_keys_on_worker():
    rows = render(
        "--set",
        "execution.quality=true,execution.online=true,payloads.enabled=true,payloads.bucket=synthetic",
        "--set",
        "execution.providers[0]=openai",
    )
    config = next(row for row in rows if row["kind"] == "ConfigMap")["data"]
    assert config["CUTBACK_QUALITY_EXECUTION"] == "true"
    assert config["CUTBACK_EXPERIMENT_EXECUTION"] == "false"
    for row in rows:
        if row["kind"] == "Deployment":
            container = row["spec"]["template"]["spec"]["containers"][0]
            assert ("openai_experiment_key" in str(container)) == (container["name"] == "worker")


def helm_binary():
    value = os.getenv("CUTBACK_HELM_BIN") or shutil.which("helm")
    local = ROOT / "artifacts/tools/helm-4.3.1/helm.exe"
    if not value and local.exists():
        value = str(local)
    if not value:
        pytest.skip("Install Helm 4 or set CUTBACK_HELM_BIN to render infrastructure contracts")
    return value


def render(*extra, expect_ok=True):
    result = subprocess.run(
        [
            helm_binary(),
            "template",
            "owned",
            str(ROOT / "infra/helm/cutback"),
            "--set",
            "image.digest=" + DIGEST,
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if not expect_ok:
        assert result.returncode != 0
        return []
    assert result.returncode == 0, result.stderr
    documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    import kubernetes_validate

    for document in documents:
        kubernetes_validate.validate(document, "1.35.0", strict=True)
    return documents


def test_chart_private_defaults_nonroot_secret_scoping_and_external_db():
    documents = render()
    assert not any(
        row["kind"] in {"Secret", "PersistentVolumeClaim", "Ingress", "LoadBalancer"}
        for row in documents
    )
    service = next(row for row in documents if row["kind"] == "Service")
    assert service["spec"]["type"] == "ClusterIP"
    config = next(row for row in documents if row["kind"] == "ConfigMap")["data"]
    for flag in (
        "CUTBACK_ALLOW_PAYLOADS",
        "CUTBACK_EXPERIMENT_EXECUTION",
        "CUTBACK_CACHE_ENABLED",
        "CUTBACK_BILLING_ENABLED",
        "CUTBACK_TRAINING_EXECUTION",
        "CUTBACK_ONLINE_EXECUTION",
        "CUTBACK_QUALITY_EXECUTION",
        "CUTBACK_WEBHOOK_EXECUTION",
    ):
        assert config[flag] == "false"
    workloads = [row for row in documents if row["kind"] in {"Deployment", "Job"}]
    assert len(workloads) == 3
    for workload in workloads:
        pod = workload["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        container = pod["containers"][0]
        assert container["image"].endswith("@" + DIGEST)
        security = container["securityContext"]
        assert security["runAsNonRoot"] and security["readOnlyRootFilesystem"]
        assert not security["allowPrivilegeEscalation"] and security["capabilities"]["drop"] == [
            "ALL"
        ]
        assert "requests" in container["resources"] and "limits" in container["resources"]
        text = str(pod)
        if container["name"] == "worker":
            assert "admin_token" not in text and "experiment_key" not in text
        if container["name"] == "migration":
            assert "admin_token" not in text and "cutback-migration" in text
    policies = [row for row in documents if row["kind"] == "NetworkPolicy"]
    assert len(policies) == 3
    assert all(row["spec"]["ingress"] == [] for row in policies)
    assert all(row["spec"]["policyTypes"] == ["Ingress", "Egress"] for row in policies)


def test_chart_refuses_unsafe_or_incomplete_opt_ins_and_mounts_only_selected_worker_keys():
    render("--set", "cache.enabled=true", expect_ok=False)
    render("--set", "execution.schedules=true", expect_ok=False)
    render("--set", "ingress.enabled=true", expect_ok=False)
    render("--set", "retention.rawDays=400", expect_ok=False)
    render("--set", "execution.online=true,worker.enabled=false", expect_ok=False)
    render("--set", "execution.quality=true", expect_ok=False)
    render("--set", "execution.quality=true,execution.online=true", expect_ok=False)
    render("--set", "notifications.enabled=true", expect_ok=False)
    rows = render(
        "--set",
        "payloads.enabled=true,payloads.bucket=synthetic-bucket,execution.experiments=true",
        "--set",
        "execution.providers[0]=openai",
    )
    for row in rows:
        if row["kind"] != "Deployment":
            continue
        pod = row["spec"]["template"]["spec"]
        container = pod["containers"][0]
        text = str(pod)
        if container["name"] == "collector":
            assert "experiment_key" not in text
        else:
            assert "openai_experiment_key" in text and "anthropic_experiment_key" not in text
        assert "payload_key" in text


def test_chart_complete_opt_ins_preserve_training_and_billing_credential_boundaries(tmp_path):
    values = {
        "image": {"digest": DIGEST},
        "payloads": {"enabled": True, "backend": "file", "existingClaim": "existing-shared-rwx"},
        "training": {
            "enabled": True,
            "imageRepository": "owned/training",
            "imageDigest": DIGEST,
            "baseModelClaim": "owned-readonly-models",
            "cpuHourlyUSD": "0",
            "models": {"owned": {"path": "/models/owned", "sha256": "b" * 64}},
        },
        "billing": {"enabled": True, "priceIDs": ["price_synthetic"]},
        "identity": {
            "issuer": "https://identity.invalid",
            "audience": "synthetic-api",
            "jwksURL": "https://identity.invalid/jwks",
            "clientID": "synthetic-client",
            "clientSecretKey": "oidc_secret",
        },
        "ingress": {
            "enabled": True,
            "className": "owned-ingress",
            "host": "collector.invalid",
            "tlsSecret": "existing-tls",
            "trustedProxies": "10.40.0.0/24",
        },
        "publicURL": "https://collector.invalid",
        "network": {
            "databasePeers": [{"ipBlock": {"cidr": "192.0.2.10/32"}}],
            "ingressPeers": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "owned-ingress"}
                    },
                    "podSelector": {"matchLabels": {"app": "ingress"}},
                }
            ],
        },
    }
    path = tmp_path / "owned-values.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    rows = render("--values", str(path))
    training = next(
        row
        for row in rows
        if row["kind"] == "Deployment" and row["metadata"]["name"].endswith("-training")
    )
    pod = training["spec"]["template"]["spec"]
    text = str(pod)
    assert "admin_token" not in text and "experiment_key" not in text and "stripe_" not in text
    assert any(
        volume["name"] == "models" and volume["readOnly"]
        for volume in pod["containers"][0]["volumeMounts"]
    )
    collector = next(
        row
        for row in rows
        if row["kind"] == "Deployment" and row["metadata"]["name"].endswith("-collector")
    )
    assert "stripe_restricted_key" in str(collector) and "oidc_client_secret" in str(collector)
    worker = next(
        row
        for row in rows
        if row["kind"] == "Deployment" and row["metadata"]["name"].endswith("-worker")
    )
    assert "stripe_" not in str(worker) and "oidc_secret" not in str(worker)
    env = worker["spec"]["template"]["spec"]["containers"][0]["env"]
    assert {row["name"]: row.get("value") for row in env}["CUTBACK_TRAINING_EXECUTION"] == "false"
