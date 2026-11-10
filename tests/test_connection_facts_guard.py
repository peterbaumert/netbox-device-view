"""Only one module reads the raw peers of a component; every other reader would ignore view permissions."""

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "netbox_device_view"
# The one reader: it keeps only the cable and the peers that the user can view.
READER = PACKAGE / "utils.py"
RAW = {"link_peers", "connected_endpoints"}


def reads_raw(node):
    if isinstance(node, ast.Attribute):
        return node.attr in RAW
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in RAW
    )


def python_reads(path):
    return [
        node.lineno for node in ast.walk(ast.parse(path.read_text())) if reads_raw(node)
    ]


def test_only_the_reader_reads_the_raw_peers():
    found = [
        f"{path.relative_to(PACKAGE)}:{line}"
        for path in sorted(PACKAGE.rglob("*.py"))
        if path != READER
        for line in python_reads(path)
    ]
    found += [
        f"{path.relative_to(PACKAGE)}:{number}"
        for path in sorted(PACKAGE.rglob("*.html"))
        for number, text in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"\.(link_peers|connected_endpoints)\b", text)
    ]
    assert found == []
