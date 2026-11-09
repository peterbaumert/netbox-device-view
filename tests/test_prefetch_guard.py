"""One module builds the cable-peer prefetches, so every component queryset loads the peers of all models."""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "netbox_device_view"


def builds_prefetch(path):
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "GenericPrefetch"
        for node in ast.walk(ast.parse(path.read_text()))
    )


def test_one_module_builds_the_generic_prefetches():
    builders = [
        str(path.relative_to(PACKAGE))
        for path in sorted(PACKAGE.rglob("*.py"))
        if builds_prefetch(path)
    ]
    assert len(builders) == 1, builders
