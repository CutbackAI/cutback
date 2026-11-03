"""Load this checkout's developer supervisor without depending on test order."""

import importlib.util
from pathlib import Path


def supervisor():
    path = Path(__file__).resolve().parents[1] / "scripts/mixed_capacity_acceptance.py"
    spec = importlib.util.spec_from_file_location("owned_mixed_supervisor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
