from cutback.scanner import scan
from cutback.scanner.python import reconcile_inventory


def test_nested_ignore_negation(tmp_path):
    child = tmp_path / "src"
    child.mkdir()
    (child / ".gitignore").write_text("*.py\n!keep.py\n")
    code = (
        'from openai import OpenAI\nc=OpenAI()\nc.responses.create(model="gpt-4.1-mini",input="x")'
    )
    (child / "hidden.py").write_text(code)
    (child / "keep.py").write_text(code)
    result = scan(tmp_path)
    assert [site["source_file"] for site in result["call_sites"]] == ["src/keep.py"]


def test_instance_factory_and_route_attribution(tmp_path):
    (tmp_path / "app.py").write_text("""from openai import OpenAI
def factory():
    return OpenAI()
class Wrapper:
    def __init__(self):
        self.client = factory()
    def call(self, text):
        return self.client.responses.create(model="gpt-4.1-mini", input=text)
""")
    (site,) = scan(tmp_path)["call_sites"]
    assert site["source_symbol"] == "Wrapper.call"
    assert site["provider"] == "openai"


def test_relative_package_import_and_move_reconciliation(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("from .clients import client\n")
    (package / "clients.py").write_text("from openai import OpenAI\nclient=OpenAI()")
    path = tmp_path / "app.py"
    path.write_text(
        "from pkg import client\ndef work(x):\n"
        ' return client.responses.create(model="gpt-4.1-mini", input=x)'
    )
    previous = scan(tmp_path)
    path.rename(tmp_path / "moved.py")
    current = scan(tmp_path)
    assert len(current["call_sites"]) == 1
    assert len(reconcile_inventory(previous, current)["links"]) == 1


def test_route_entry_point(tmp_path):
    (tmp_path / "app.py").write_text("""from openai import OpenAI
c=OpenAI()
def shared(x):
    return c.responses.create(model="gpt-4.1-mini",input=x)
@app.post("/classify")
def route(x):
    return shared(x)
""")
    (site,) = scan(tmp_path)["call_sites"]
    assert site["entry_points"][0]["symbol"] == "app.route"
