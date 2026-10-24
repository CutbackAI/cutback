from cutback.scanner import scan


def test_lazy_client_delegation_and_parameter_flow_is_heuristic(tmp_path):
    (tmp_path / "app.py").write_text("""from openai import OpenAI
class Lazy:
 def __init__(self):
  self._client=None
 def _get(self):
  if self._client is None:
   self._client=OpenAI()
  return self._client
 def __getattr__(self,name):
  return getattr(self._get(),name)
client=Lazy()
def call(openai_client, text):
 return openai_client.chat.completions.create(model="gpt-4.1",messages=text)
def route(text):
 return call(None or client,text)
""")
    result = scan(tmp_path)
    assert len(result["call_sites"]) == 1
    site = result["call_sites"][0]
    assert site["provider"] == "openai" and site["confidence"] == "medium"
    assert "caller_parameter_flow_requires_runtime_confirmation" in site["unresolved"]
    assert site["actual_tokens"] is None
