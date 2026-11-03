"""Qualify installed diagnosis in a non-root, read-only, network-disabled local image."""

import argparse
import json
import re
import subprocess
from uuid import uuid4

FIXTURE = """import json,os,socket,tempfile
from pathlib import Path
from cutback.application_checks import git
from cutback.application_readiness import diagnose
from cutback.sample_app.policy_fixture import fixture
assert os.getuid()==10001
def forbidden(*args,**kwargs):
 raise AssertionError("READINESS_MUST_NOT_CONNECT")
socket.socket.connect=forbidden
socket.create_connection=forbidden
with tempfile.TemporaryDirectory(prefix="owned-readiness-") as name:
 root=Path(name)/"application"
 root.mkdir()
 source=('from openai import OpenAI\\nclient=OpenAI()\\ndef classify(text):\\n'
         '    return client.responses.create(model="gpt-4.1",input=text,max_output_tokens=64)\\n'
         'raise RuntimeError("owned-import-sentinel")\\n')
 (root/"app.py").write_text(source)
 (root/".gitignore").write_text(".cutback/\\n.env\\n")
 (root/".env").write_text("SECRET=owned-env-sentinel\\n")
 (root/"check_application.py").write_text('if __name__ == "__main__":\\n'
     '    raise RuntimeError("owned-check-sentinel")\\n')
 git(root,"init","--template=")
 git(root,"add",".")
 git(root,"-c","user.name=Synthetic","-c","user.email=synthetic@example.invalid",
     "commit","-m","Independent readiness fixture")
 ready=diagnose(root)
 assert ready["readiness"]=="REVIEW_PREPARATION_INPUTS"
 assert ready["application_checks"]["candidate_commands"]==1
 assert ready["application_checks"]["workflow_coverage"]=="unknown"
 assert ready["provider_calls"]==ready["application_commands_executed"]==0
 assert (root/"app.py").read_text()==source
 assert "owned-env-sentinel" not in json.dumps(ready)
 assert "owned-import-sentinel" not in json.dumps(ready)
 assert "owned-check-sentinel" not in json.dumps(ready)
 (root/"app.py").write_text(source.replace("max_output_tokens=64","**runtime_options"))
 blocked=diagnose(root)
 assert blocked["readiness"]=="SOURCE_ADAPTER_REQUIRED"
 assert blocked["source_contracts"]["issues"][0]["diagnostic_code"]==(
     "EXPANDED_REQUEST_ARGUMENTS_REQUIRE_REVIEW")
 policy=Path(name)/"policy"
 policy.mkdir()
 for filename,value in fixture().items():
  (policy/filename).write_text(value,encoding="utf-8")
 git(policy,"init","--template=")
 git(policy,"add",".")
 git(policy,"-c","user.name=Synthetic","-c","user.email=synthetic@example.invalid",
     "commit","-m","Independent whole-policy fixture")
 whole=diagnose(policy)
 assert whole["workflow_choices"][0]["modes"]==["source_policy_program"]
 assert whole["next_actions"][0]["argv"]==["cutback","source-policy","prepare","--help"]
 assert whole["startup"]["fixed_policy_consumers"]==[
     {"source_file":"app.py","status":"EXTERNAL_PROCESS_STARTUP_REQUIRED"}]
 alias=Path(name)/"symlink-root"
 alias.symlink_to(policy,target_is_directory=True)
 from cutback.application_check_discovery import discover
 from cutback.application_checks import ApplicationCommand,prepare
 for action in (lambda:diagnose(alias),lambda:discover(alias,root),
                lambda:prepare(alias,root,[ApplicationCommand(key="owned",arguments=["x.py"])])):
  try:action()
  except ValueError as error:assert str(error)=="APPLICATION_CHECK_SYMLINK_ROOT_DENIED"
  else:raise AssertionError("SYMLINK_ROOT_NOT_REJECTED")
 unsafe=Path(name)/"workspace-root"
 unsafe.mkdir()
 outside=Path(name)/"outside"
 outside.mkdir()
 (unsafe/".cutback").symlink_to(outside,target_is_directory=True)
 try:diagnose(unsafe)
 except ValueError as error:assert str(error)=="UNSAFE_WORKSPACE_DIRECTORY"
 else:raise AssertionError("SYMLINK_WORKSPACE_NOT_REJECTED")
 assert not list(outside.iterdir())
print(json.dumps({"contract":"cutback.application-readiness-image.v1","uid":10001,
 "qualified_request_and_check_candidates":True,"unsupported_request_preserved":True,
 "whole_policy_wrapper_preserved":True,"application_imported":False,
 "application_commands_executed":0,"paid_provider_calls":0,"network":"none",
 "linux_symlink_root_and_workspace_guards":True}))
"""


def qualify(image, *, checked):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("EXACT_LOCAL_IMAGE_ID_REQUIRED")
    owner = uuid4().hex
    name = "cutback-readiness-" + owner
    try:
        output = checked(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--label",
                "com.cutback.readiness.owner=" + owner,
                "--pull",
                "never",
                "-i",
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
                "--memory",
                "256m",
                "--pids-limit",
                "128",
                "--cpus",
                "1",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--entrypoint",
                "/usr/bin/timeout",
                image,
                "--signal=KILL",
                "--kill-after=2s",
                "80s",
                "/app/.venv/bin/python",
                "-c",
                "import sys;exec(compile(sys.stdin.read(),'<owned-readiness-fixture>','exec'))",
            ],
            source=FIXTURE,
            timeout=90,
        )
    finally:
        cleanup(name, owner)
    return {
        **json.loads(output.strip().splitlines()[-1]),
        "image_id": image,
        "container_cleanup_confirmed": True,
    }


def cleanup(name, owner):
    """Remove only this invocation's labeled container, then confirm absence."""

    def call(*arguments):
        try:
            return subprocess.run(
                ["docker", *arguments], text=True, capture_output=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("READINESS_CONTAINER_CLEANUP_UNCONFIRMED") from None

    inspected = call(
        "container",
        "inspect",
        "--format",
        '{"id":{{json .Id}},"labels":{{json .Config.Labels}}}',
        name,
    )
    if inspected.returncode == 0:
        try:
            metadata = json.loads(inspected.stdout)
            identifier = metadata["id"]
            owned = metadata["labels"].get("com.cutback.readiness.owner") == owner and re.fullmatch(
                r"[a-f0-9]{64}", identifier
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            owned = False
        if not owned:
            raise ValueError("READINESS_CONTAINER_OWNERSHIP_CONFLICT")
        call("container", "rm", "--force", identifier)
    remaining = call("container", "ls", "--all", "--format", "{{.Names}}")
    if remaining.returncode or name in remaining.stdout.splitlines():
        raise ValueError("READINESS_CONTAINER_CLEANUP_UNCONFIRMED")


def checked(arguments, *, source, timeout):
    result = subprocess.run(
        arguments, input=source, text=True, capture_output=True, timeout=timeout
    )
    if result.returncode:
        raise ValueError("APPLICATION_READINESS_IMAGE_FAILED_DETAILS_SUPPRESSED")
    return result.stdout


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", required=True, help="Reviewed local image ID; never pulls an image."
    )
    args = parser.parse_args()
    print(json.dumps(qualify(args.image, checked=checked)))
