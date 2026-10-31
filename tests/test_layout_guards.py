"""Source checks for the layout package; pure unit tests, no Django needed."""

import ast
from pathlib import Path

LAYOUT = Path(__file__).resolve().parents[1] / "netbox_device_view" / "layout"


def _dicts_keyed_by_element_key(tree):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.DictComp)
            and isinstance(node.key, ast.Attribute)
            and node.key.attr == "key"
        ):
            yield node.lineno


def test_elements_are_merged_only_through_replace_by_key():
    # A dict keyed by element key keeps one element per key; repeated keys are valid.
    sites = [
        f"{path.relative_to(LAYOUT)}:{line}"
        for path in sorted(LAYOUT.rglob("*.py"))
        for line in _dicts_keyed_by_element_key(ast.parse(path.read_text()))
    ]
    assert sites == []
