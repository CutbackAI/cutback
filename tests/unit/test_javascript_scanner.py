import json

from cutback.core.inventory import Inventory
from cutback.scanner import scan


def test_typescript_parse_alias_scope_privacy_and_stable_identity(tmp_path):
    path = tmp_path / "app.ts"
    source = """import {OpenAI as Native} from "openai";
const model = "gpt-4.1-mini";
const client = new Native();
export async function classify(text: string) {
 return client.responses.create({model, input: "PRIVATE PROMPT", max_output_tokens: 100});
}
function shadow(client: any) { return client.responses.create({model:"fake"}); }
function unrelated() { const client = {responses:{create:()=>0}}; client.responses.create({}); }
"""
    path.write_text(source, encoding="utf-8")
    result = scan(tmp_path, languages=["typescript"])
    Inventory.model_validate(result)
    assert len(result["call_sites"]) == 1
    site = result["call_sites"][0]
    assert site["requested_model"] == "gpt-4.1-mini"
    assert site["configured_max_output_tokens"] == 100
    assert site["language"] == "typescript" and site["source_symbol"] == "classify"
    assert "PRIVATE PROMPT" not in json.dumps(result)
    path.write_text("\n\n" + source, encoding="utf-8")
    assert (
        scan(tmp_path, languages=["typescript"])["call_sites"][0]["call_site_id"]
        == site["call_site_id"]
    )
    assert not scan(tmp_path)["call_sites"]


def test_javascript_commonjs_spreads_mutations_nested_ignores_and_invalid_source(tmp_path):
    (tmp_path / "app.cjs").write_text(
        """const OpenAI = require("openai");
const client = new OpenAI(); let model="gpt-4.1"; model=process.env.MODEL;
client.chat.completions.create({model,messages:[]});
client.responses.create({model:"gpt-4.1-mini",...dynamic});
function hidden(require) { const Native=require("openai");
 const c=new Native();c.responses.create({}); }
""",
        encoding="utf-8",
    )
    folder = tmp_path / "nested"
    folder.mkdir()
    (folder / ".gitignore").write_text("ignored.ts\n", encoding="utf-8")
    (folder / "ignored.ts").write_text(
        'import OpenAI from "openai";const c=new OpenAI();c.responses.create({});', encoding="utf-8"
    )
    (tmp_path / "broken.jsx").write_text("function {{", encoding="utf-8")
    result = scan(tmp_path, languages=["javascript", "typescript"])
    assert len(result["call_sites"]) == 2
    assert all(site["requested_model"] is None for site in result["call_sites"])
    assert any(warning["code"] == "UNPARSED_JAVASCRIPT" for warning in result["warnings"])
    assert result["coverage"]["analyzed_javascript_files"] == 1


def test_anthropic_namespace_and_sdk_like_nonproviders(tmp_path):
    (tmp_path / "index.js").write_text(
        """import * as SDK from "@anthropic-ai/sdk";
import OpenAI from "unrelated-package";
const client=new SDK.Anthropic();const fake=new OpenAI();
client.messages.create({model:"claude-haiku-4-5",messages:[]});
fake.responses.create({model:"no"});
""",
        encoding="utf-8",
    )
    result = scan(tmp_path, languages=["javascript"])
    assert len(result["call_sites"]) == 1
    assert result["call_sites"][0]["provider"] == "anthropic"
