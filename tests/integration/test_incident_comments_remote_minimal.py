"""Independent core/HTTPX-only process runs the actual collaboration APIs."""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from support_http import owned_collector
from support_incident_comments import collaboration as collaboration
from support_remote_agent import remote_environment, remote_interpreter, remote_json

pytestmark = pytest.mark.postgres


def test_http_only_installed_comment_sample_has_no_sdk_server_or_crypto_dependencies(
    api, tenant, collaboration, tmp_path
):
    configured = os.getenv("CUTBACK_TEST_REMOTE_PYTHON")
    if not configured:
        pytest.skip("matching core-plus-HTTPX wheel required for independent comments acceptance")
    executable, env = remote_interpreter(configured), remote_environment()
    sample = Path(__file__).resolve().parents[2] / "examples/incident-collaboration/sample.py"
    issued = api[0].post(
        f"/v1/projects/{tenant[0]}/keys", headers=api[1], json={"role": "project_admin"}
    )
    assert issued.status_code == 201
    key = issued.json()
    env.update(
        CUTBACK_PROJECT_ID=tenant[0],
        CUTBACK_INCIDENT_ID=str(collaboration["incident"]),
        CUTBACK_COMMENT_POLICY_ID=collaboration["policy"],
        CUTBACK_CONTROL_KEY=key["token"],
    )
    receipt = tmp_path / "independent-private/receipt.json"
    with owned_collector(api[2]) as endpoint:
        env["CUTBACK_ENDPOINT"] = endpoint
        code = (
            "import importlib.util,sys,runpy,httpx; from pathlib import Path; import cutback\n"
            "assert Path(cutback.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())\n"
            "assert all(importlib.util.find_spec(n) is None for n in "
            "('openai','anthropic','fastapi','sqlalchemy','alembic','psycopg','cryptography','pytest'))\n"
            "original=httpx.Client.send\n"
            "def owned(self,request,*args,**kwargs):\n"
            " assert request.url.host=='127.0.0.1' and request.url.port=="
            + str(urlsplit(endpoint).port)
            + ", 'COMMENT_SAMPLE_EXTERNAL_REQUEST_FORBIDDEN'\n"
            " return original(self,request,*args,**kwargs)\n"
            "httpx.Client.send=owned\n"
            "script,receipt=sys.argv[1:]\n"
            "sys.argv=[script,'--receipt',receipt,'--execute-local']\n"
            "runpy.run_path(script,run_name='__main__')\n"
        )
        command = [str(executable), "-I", "-c", code, str(sample), str(receipt)]
        result = subprocess.run(
            command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60
        )
        if key["token"] in result.stdout + result.stderr:
            pytest.fail("INDEPENDENT_COMMENT_KEY_DISCLOSED", pytrace=False)
        assert result.returncode == 0, "INDEPENDENT_COMMENT_SAMPLE_FAILED"
        assert remote_json(result)["status"] == "COMPLETED_SYNTHETIC_COMMENT_LIFECYCLE"
        assert key["token"] not in receipt.read_text()
        assert (
            api[0]
            .delete(f"/v1/projects/{tenant[0]}/keys/{key['key_id']}", headers=api[1])
            .status_code
            == 200
        )
        denied = subprocess.run(
            command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60
        )
        assert denied.returncode == 1
        assert key["token"] not in denied.stdout + denied.stderr
