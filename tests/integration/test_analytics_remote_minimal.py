"""An independent HTTP-only installed client uses current scoped rights, with no SDKs."""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from support_http import owned_collector
from support_remote_agent import remote_environment, remote_interpreter, remote_json

pytestmark = pytest.mark.postgres


def test_installed_http_only_analytics_sample_and_revoked_new_intent(api, tenant, tmp_path):
    configured = os.getenv("CUTBACK_TEST_REMOTE_PYTHON")
    if not configured:
        pytest.skip("matching core-plus-HTTPX wheel required for independent analytics acceptance")
    executable, env = remote_interpreter(configured), remote_environment()
    sample = Path(__file__).resolve().parents[2] / "examples/shared-analytics/sample.py"
    issued = api[0].post(f"/v1/projects/{tenant[0]}/keys", headers=api[1], json={"role": "editor"})
    assert issued.status_code == 201
    key = issued.json()
    env["CUTBACK_CONTROL_TOKEN"] = key["token"]
    receipt = tmp_path / ".cutback/independent.json"
    with owned_collector(api[2]) as endpoint:
        code = (
            "import importlib.util,sys,runpy,httpx; from pathlib import Path; import cutback\n"
            "assert Path(cutback.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())\n"
            "assert all(importlib.util.find_spec(n) is None for n in "
            "('openai','anthropic','fastapi','sqlalchemy','alembic','psycopg','cryptography','pytest'))\n"
            "original=httpx.Client.send\n"
            "def owned(self,request,*args,**kwargs):\n"
            " assert request.url.host=='127.0.0.1' and request.url.port=="
            + str(urlsplit(endpoint).port)
            + ", 'ANALYTICS_SAMPLE_EXTERNAL_REQUEST_FORBIDDEN'\n"
            " return original(self,request,*args,**kwargs)\n"
            "httpx.Client.send=owned\n"
            "script,endpoint,project,receipt=sys.argv[1:]\n"
            "sys.argv=[script,'--endpoint',endpoint,'--project',project,'--receipt',receipt,"
            "'--execute-local']\n"
            "runpy.run_path(script,run_name='__main__')\n"
        )
        command = [
            str(executable),
            "-I",
            "-c",
            code,
            str(sample),
            endpoint,
            tenant[0],
            str(receipt),
        ]
        result = subprocess.run(
            command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60
        )
        if key["token"] in result.stdout + result.stderr:
            pytest.fail("INDEPENDENT_ANALYTICS_KEY_DISCLOSED", pytrace=False)
        assert result.returncode == 0, "INDEPENDENT_ANALYTICS_SAMPLE_FAILED"
        assert remote_json(result)["status"] == "COMPLETE"
        assert key["token"] not in receipt.read_text()
        assert (
            api[0]
            .delete(f"/v1/projects/{tenant[0]}/keys/{key['key_id']}", headers=api[1])
            .status_code
            == 200
        )
        # A completed receipt is historical. Use a distinct explicit intent to
        # verify live server revocation rather than treating the receipt as a probe.
        denied = subprocess.run(
            [*command[:-1], str(tmp_path / ".cutback/revoked.json")],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert key["token"] not in denied.stdout + denied.stderr
        assert denied.returncode == 1
        assert remote_json(denied)["code"] == "ANALYTICS_EXAMPLE_CURRENT_RIGHTS_OR_STATE_REFUSED"
