"""Shared setup: import paths (plant/, dcs/, common/, tools/) and the committed inventory."""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("common", "dcs", "plant", "tools", "dataops"):
    p = os.path.join(ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def read(rel_path):
    with open(os.path.join(ROOT, rel_path), encoding="utf-8") as f:
        return f.read()


LAYERS = ("field", "dcs", "opcua", "plant", "api")


@pytest.fixture(scope="session")
def inv():
    """The committed inventory (the contract): {"field": ..., "dcs": ..., "opcua": ..., "plant": ..., "api": ...}."""
    return {name: json.loads(read(f"inventory/{name}.json")) for name in LAYERS}


@pytest.fixture(scope="session")
def code():
    """The same, generated from the code as it is now (so contract tests don't depend on regenerating)."""
    import inventory
    return {name: json.loads(text) for name, text in
            ((n[:-5], t) for n, t in inventory.build().items() if n.endswith(".json"))}
