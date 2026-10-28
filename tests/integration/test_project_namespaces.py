from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_project_names_are_organization_scoped_while_keys_remain_project_scoped(api):
    client, admin, _ = api
    organizations = [
        client.post("/v1/organizations", headers=admin, json={"name": name}).json()["id"]
        for name in ("Owned namespace A", "Owned namespace B")
    ]
    slug = "shared-project-" + uuid4().hex
    projects = []
    for organization in organizations:
        response = client.post(
            "/v1/projects", headers=admin, json={"slug": slug, "organization_id": organization}
        )
        assert response.status_code == 201, response.text
        projects.append(response.json()["id"])
        assert (
            client.post(
                "/v1/projects", headers=admin, json={"slug": slug, "organization_id": organization}
            ).status_code
            == 409
        )
    from importlib.resources import files

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    with pytest.raises(ValueError, match="DOWNGRADE_REQUIRES_UNIQUE_LEGACY_PROJECT_NAMES"):
        command.downgrade(cfg, "0003")
    with api[2].state.engine.connect() as connection:
        from cutback.server.migrate import CURRENT_REVISION

        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_REVISION
        )
        assert connection.scalar(text("SELECT pg_try_advisory_lock(1638366142)")) is True
        connection.execute(text("SELECT pg_advisory_unlock(1638366142)"))
    key = client.post(
        f"/v1/projects/{projects[0]}/keys", headers=admin, json={"role": "viewer"}
    ).json()["token"]
    viewer = {"Authorization": "Bearer " + key}
    metadata = client.get(f"/v1/projects/{projects[0]}", headers=viewer)
    assert metadata.status_code == 200 and metadata.json()["organization_id"] == organizations[0]
    assert client.get(f"/v1/projects/{projects[1]}", headers=viewer).status_code == 403
    assert client.get(f"/v1/projects/{projects[1]}/usage", headers=viewer).status_code == 403
    for project in projects:
        assert (
            client.delete(
                f"/v1/projects/{project}", headers=admin, params={"confirm_slug": slug}
            ).status_code
            == 200
        )
