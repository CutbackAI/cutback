"""Inspect committed Compose references without starting or configuring services."""

from pathlib import Path

import yaml


def test_worker_has_no_installation_administrator_mount_in_both_compose_variants():
    root = Path(__file__).resolve().parents[2] / "infra/compose"
    for name in ("docker-compose.yml", "docker-compose.tls.yml"):
        value = yaml.safe_load((root / name).read_text(encoding="utf-8"))
        worker = value["services"]["worker"]
        assert worker["secrets"] == ["db_password"]
        assert "CUTBACK_ADMIN_TOKEN_FILE" not in worker["environment"]
        assert worker["environment"]["CUTBACK_SEMANTIC_CACHE_EXECUTION"].endswith(":-false}")
        assert worker["environment"]["CUTBACK_BATCH_EXECUTION"].endswith(":-false}")
        assert value["services"]["collector"]["environment"][
            "CUTBACK_SEMANTIC_CACHE_ENABLED"
        ].endswith(":-false}")
