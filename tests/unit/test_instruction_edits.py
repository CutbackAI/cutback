import ast
import hashlib
import subprocess

import pytest
from test_request_profiles import SOURCE, inspect, recommendation

from cutback.changes import instruction_patch, model_patch
from cutback.core.instruction_edits import qualify_instruction_edit


@pytest.mark.parametrize(
    "kind",
    ["replace", "insert", "unicode", "multiline", "global-model", "crlf", "no-final-newline"],
)
def test_qualified_instruction_diff_is_exact_reversible_and_never_executes_source(
    tmp_path, monkeypatch, kind
):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("NETWORK_OR_APPLICATION_EXECUTION_FORBIDDEN")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    source = SOURCE
    if kind == "insert":
        source = source.replace('instructions="Private synthetic task",', "")
    elif kind == "unicode":
        source = source.replace('instructions="Private synthetic task"', 'instructions="é α task"')
    elif kind == "multiline":
        source = source.replace('"Private synthetic task"', '"""First line\nSecond line"""')
    elif kind == "global-model":
        source = source.replace("client=OpenAI()", 'MODEL="gpt-4.1"\nclient=OpenAI()').replace(
            'model="gpt-4.1"', "model=MODEL"
        )
    elif kind == "no-final-newline":
        source = source.rstrip("\n")
    inventory, site, profile = inspect(tmp_path, source)
    if kind == "crlf":
        (tmp_path / "app.py").write_bytes(source.replace("\n", "\r\n").encode())
        inventory, site, profile = inspect_bytes(tmp_path)
    before = (tmp_path / "app.py").read_bytes()
    binding = qualify_instruction_edit(tmp_path, site, profile)
    assert binding.mode == ("insert" if kind == "insert" else "replace")
    record = recommendation(profile)
    instruction = "Return A only. é '); __import__('os').system('DO_NOT_EXECUTE'); #\n"
    record["evidence"]["candidate"].update(instruction=instruction, max_output_tokens=32)
    with pytest.raises(ValueError, match="PROMPT_VARIANT_REQUIRES_SEPARATE"):
        model_patch(
            tmp_path,
            inventory,
            site["call_site_id"],
            record,
            expected_source_sha256=hashlib.sha256(before).hexdigest(),
        )
    artifact = instruction_patch(
        tmp_path,
        inventory,
        site["call_site_id"],
        record,
        expected_source_sha256=hashlib.sha256(before).hexdigest(),
        instruction_edit=binding,
        max_instruction_bytes=1024,
    )
    assert (tmp_path / "app.py").read_bytes() == before
    assert set(artifact["transformations"]) == {"instruction", "model", "output_cap"}
    assert artifact["instruction_edit"] == binding.model_dump(mode="json")
    assert not artifact["production_deployed"]
    for content in (artifact["diff"], artifact["rollback_diff"]):
        applied = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", "-"],
            cwd=tmp_path,
            input=content.encode(),
            capture_output=True,
        )
        assert applied.returncode == 0, applied.stderr.decode(errors="replace")
        if content == artifact["diff"]:
            raw = (tmp_path / "app.py").read_bytes()
            assert hashlib.sha256(raw).hexdigest() == artifact["verification"]["after_sha256"]
            tree = ast.parse(raw.decode())
            call = next(
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call) and any(k.arg == "instructions" for k in n.keywords)
            )
            values = {k.arg: k.value for k in call.keywords}
            assert values["instructions"].value == instruction
            assert values["max_output_tokens"].value == 32
    assert (tmp_path / "app.py").read_bytes() == before


def inspect_bytes(root):
    from cutback.core.request_profiles import prepare_profile
    from cutback.scanner import scan

    inventory = scan(root)
    site = inventory["call_sites"][0]
    return inventory, site, prepare_profile(root, site, commit=inventory["project"].get("commit"))


def test_stale_binding_bounds_and_unrelated_request_changes_refuse_without_mutation(tmp_path):
    inventory, site, profile = inspect(tmp_path, SOURCE)
    binding = qualify_instruction_edit(tmp_path, site, profile)
    record = recommendation(profile)
    record["evidence"]["candidate"]["instruction"] = "A bounded new instruction"
    before = (tmp_path / "app.py").read_bytes()

    def attempt(edit=binding, bound=1024):
        return instruction_patch(
            tmp_path,
            inventory,
            site["call_site_id"],
            record,
            expected_source_sha256=hashlib.sha256(before).hexdigest(),
            instruction_edit=edit,
            max_instruction_bytes=bound,
        )

    with pytest.raises(ValueError, match="CHANGED_SINCE_CAMPAIGN"):
        attempt(binding.model_copy(update={"start": binding.start + 1}))
    with pytest.raises(ValueError, match="OUTSIDE_APPROVED_BYTE_BOUND"):
        attempt(bound=1)
    from copy import deepcopy

    record["evidence"]["candidate"]["request_contract"] = deepcopy(
        record["evidence"]["candidate"]["request_contract"]
    )
    record["evidence"]["candidate"]["request_contract"]["options"]["text"] = {
        "format": {"type": "text"}
    }
    with pytest.raises(ValueError, match="REQUEST_VARIANT_REQUIRES_SEPARATE_PATCH"):
        attempt()
    assert (tmp_path / "app.py").read_bytes() == before


def test_dynamic_instruction_requires_its_own_qualified_adapter(tmp_path):
    source = SOURCE.replace(
        "client=OpenAI()", 'RULE="Private synthetic task"\nclient=OpenAI()'
    ).replace('instructions="Private synthetic task"', "instructions=RULE")
    _, site, profile = inspect(tmp_path, source)
    with pytest.raises(ValueError, match="DYNAMIC_INSTRUCTION_EDIT_REQUIRES_SEPARATE_ADAPTER"):
        qualify_instruction_edit(tmp_path, site, profile)
