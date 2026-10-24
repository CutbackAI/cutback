from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgres


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
