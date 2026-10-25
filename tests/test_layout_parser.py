"""
Tests for netbox_device_view.layout.parser

These are pure unit tests — no Django, no DB, no NetBox required.
"""

import ast
import copy
from pathlib import Path

import pytest
import yaml as pyyaml

from netbox_device_view.layout import parser, render_css
from netbox_device_view.layout.model import (
    ElementKind,
    Face,
)
from netbox_device_view.layout.parser import (
    LayoutParseError,
    parse,
    validate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(yaml_text: str):
    """Parse yaml_text and return a NormalizedLayout."""
    return parse(yaml_text)


# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_1_accepted(self):
        layout = _parse("version: 1\nviews: {}")
        assert layout.source == "yaml"

    def test_missing_version_defaults_to_1(self):
        layout = _parse("views: {}")
        assert layout.source == "yaml"

    def test_unsupported_version_raises(self):
        with pytest.raises(LayoutParseError, match="Unsupported layout version"):
            _parse("version: 99\nviews: {}")

    @pytest.mark.parametrize("value", ["abc", "null", "[1]", ".inf", ".nan"])
    def test_non_integer_version_raises_parse_error(self, value):
        with pytest.raises(LayoutParseError, match="version: must be an integer"):
            _parse(f"version: {value}\nviews: {{}}")

    def test_non_integer_field_raises_parse_error(self):
        with pytest.raises(
            LayoutParseError, match="canvas.columns: must be an integer"
        ):
            _parse("canvas:\n  columns: abc\nviews: {}")

    def test_non_mapping_raises(self):
        with pytest.raises(LayoutParseError, match="must be a YAML mapping"):
            _parse("- item1\n- item2")

    def test_invalid_yaml_raises(self):
        with pytest.raises(LayoutParseError, match="Invalid YAML"):
            _parse("key: [unclosed")


# ---------------------------------------------------------------------------
# Meta / description
# ---------------------------------------------------------------------------


class TestMeta:
    def test_description_captured(self):
        layout = _parse('version: 1\nmeta:\n  description: "My Device"\nviews: {}')
        assert layout.description == "My Device"

    def test_no_meta_gives_empty_description(self):
        layout = _parse("version: 1\nviews: {}")
        assert layout.description == ""

    def test_global_background(self):
        yaml = """
version: 1
meta:
  background: "#aabbcc"
canvas:
  columns: 4
  rows: 1
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "p"
          start: 1
          count: 4
          pattern: all
"""
        layout = _parse(yaml)
        assert layout.front is not None
        assert layout.front.canvas.background == "#aabbcc"


# ---------------------------------------------------------------------------
# Canvas defaults and override
# ---------------------------------------------------------------------------


class TestCanvas:
    def test_defaults(self):
        layout = _parse("version: 1\nviews: {}")
        # No views → no canvas to test; but parse should not fail
        assert layout is not None

    def test_canvas_columns_rows(self):
        yaml = """
version: 1
canvas:
  columns: 8
  rows: 1
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "p"
          start: 1
          count: 4
          pattern: all
"""
        layout = _parse(yaml)
        assert layout.front.canvas.columns == 8
        assert layout.front.canvas.rows == 1

    def test_canvas_background_propagates(self):
        yaml = """
version: 1
canvas:
  background: "#ff0000"
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "p"
          start: 1
          count: 2
          pattern: all
"""
        layout = _parse(yaml)
        assert layout.front.canvas.background == "#ff0000"


# ---------------------------------------------------------------------------
# Face parsing
# ---------------------------------------------------------------------------


class TestFaceParsing:
    def test_front_view(self):
        yaml = "version: 1\nviews:\n  front:\n    rows: []\n"
        layout = _parse(yaml)
        assert "front" in layout.views
        assert layout.views["front"].face == Face.FRONT

    def test_rear_view(self):
        yaml = "version: 1\nviews:\n  rear:\n    rows: []\n"
        layout = _parse(yaml)
        assert "rear" in layout.views
        assert layout.views["rear"].face == Face.REAR

    def test_has_separate_faces_both(self):
        yaml = "version: 1\nviews:\n  front:\n    rows: []\n  rear:\n    rows: []\n"
        layout = _parse(yaml)
        assert layout.has_separate_faces()

    def test_has_separate_faces_front_only(self):
        yaml = "version: 1\nviews:\n  front:\n    rows: []\n"
        layout = _parse(yaml)
        assert not layout.has_separate_faces()


# ---------------------------------------------------------------------------
# Sequence expansion — pattern: all
# ---------------------------------------------------------------------------


SIMPLE_ALL_YAML = """
version: 1
canvas:
  columns: 6
  rows: 1
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "port-"
          start: 1
          count: 6
          pattern: all
"""


class TestSequenceAll:
    def setup_method(self):
        self.layout = _parse(SIMPLE_ALL_YAML)
        self.elements = self.layout.front.elements

    def test_count(self):
        assert len(self.elements) == 6

    def test_keys(self):
        keys = [e.key for e in self.elements]
        assert keys == ["port-1", "port-2", "port-3", "port-4", "port-5", "port-6"]

    def test_all_in_row_1(self):
        assert all(e.row == 1 for e in self.elements)

    def test_sequential_columns(self):
        cols = [e.col for e in sorted(self.elements, key=lambda e: e.key)]
        assert cols == list(range(1, 7))

    def test_kind_is_port(self):
        assert all(e.kind == ElementKind.PORT for e in self.elements)


# ---------------------------------------------------------------------------
# Sequence expansion — pattern: all with explicit row: 2
# ---------------------------------------------------------------------------


ALL_ROW2_YAML = """
version: 1
canvas:
  columns: 6
  rows: 2
views:
  front:
    rows:
      - sequence:
          kind: interface
          prefix: "uplink-"
          start: 1
          count: 4
          pattern: all
          row: 2
"""


class TestSequenceAllRow2:
    def setup_method(self):
        self.layout = _parse(ALL_ROW2_YAML)
        self.elements = self.layout.front.elements

    def test_count(self):
        assert len(self.elements) == 4

    def test_all_in_row_2(self):
        assert all(e.row == 2 for e in self.elements)

    def test_sequential_columns(self):
        cols = sorted(e.col for e in self.elements)
        assert cols == [1, 2, 3, 4]

    def test_keys(self):
        keys = [e.key for e in self.elements]
        assert keys == ["uplink-1", "uplink-2", "uplink-3", "uplink-4"]


# ---------------------------------------------------------------------------
# Sequence expansion — pattern: top-odd (2-row Cisco style)
# ---------------------------------------------------------------------------


ODD_YAML = """
version: 1
canvas:
  columns: 12
  rows: 2
views:
  front:
    rows:
      - sequence:
          kind: interface
          prefix: "gi0-"
          start: 1
          count: 12
          pattern: top-odd
"""


class TestSequenceOdd:
    def setup_method(self):
        self.layout = _parse(ODD_YAML)
        self.elements = self.layout.front.elements

    def test_count(self):
        assert len(self.elements) == 12

    def test_odd_ports_in_row_1(self):
        odd_els = [e for e in self.elements if int(e.key.split("-")[1]) % 2 == 1]
        assert all(e.row == 1 for e in odd_els)

    def test_even_ports_in_row_2(self):
        even_els = [e for e in self.elements if int(e.key.split("-")[1]) % 2 == 0]
        assert all(e.row == 2 for e in even_els)

    def test_kind_is_interface(self):
        assert all(e.kind == ElementKind.INTERFACE for e in self.elements)


# ---------------------------------------------------------------------------
# Sequence expansion — pattern: top-even
# ---------------------------------------------------------------------------


EVEN_YAML = """
version: 1
canvas:
  columns: 6
  rows: 2
views:
  front:
    rows:
      - sequence:
          kind: interface
          prefix: "gi0-"
          start: 1
          count: 6
          pattern: top-even
"""


class TestSequenceEven:
    def setup_method(self):
        self.layout = _parse(EVEN_YAML)
        self.elements = self.layout.front.elements

    def test_even_ports_in_row_1(self):
        # "even" pattern: top-even-numbered ports go in row 1
        even_els = [e for e in self.elements if int(e.key.split("-")[1]) % 2 == 0]
        assert all(e.row == 1 for e in even_els)

    def test_odd_ports_in_row_2(self):
        odd_els = [e for e in self.elements if int(e.key.split("-")[1]) % 2 == 1]
        assert all(e.row == 2 for e in odd_els)


# ---------------------------------------------------------------------------
# Unknown pattern raises
# ---------------------------------------------------------------------------


class TestUnknownPattern:
    def test_raises(self):
        yaml = """
version: 1
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "p"
          start: 1
          count: 2
          pattern: diagonal
"""
        with pytest.raises(LayoutParseError, match="Unknown sequence pattern"):
            _parse(yaml)


# ---------------------------------------------------------------------------
# Spacer / blank insertion
# ---------------------------------------------------------------------------


class TestSpacerBlank:
    def test_spacer_spans_all_rows(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 2
views:
  front:
    rows:
      - spacer: 2
"""
        layout = _parse(yaml)
        spacers = [e for e in layout.front.elements if e.kind == ElementKind.SPACER]
        assert len(spacers) == 2
        # Each spacer should span all canvas rows (row_span == 2)
        assert all(e.row_span == 2 for e in spacers)

    def test_blank_spans_all_rows(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 2
views:
  front:
    rows:
      - blank: 3
"""
        layout = _parse(yaml)
        blanks = [e for e in layout.front.elements if e.kind == ElementKind.BLANK]
        assert len(blanks) == 3
        assert all(e.row_span == 2 for e in blanks)


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


class TestGroup:
    def test_group_inserts_spacer_between_sections(self):
        yaml = """
version: 1
canvas:
  columns: 8
  rows: 1
views:
  front:
    rows:
      - group:
          spacer: 1
          sections:
            - sequence:
                kind: port
                prefix: "p"
                start: 1
                count: 3
                pattern: all
            - sequence:
                kind: port
                prefix: "p"
                start: 4
                count: 3
                pattern: all
"""
        layout = _parse(yaml)
        elements = layout.front.elements
        spacers = [e for e in elements if e.kind == ElementKind.SPACER]
        ports = [e for e in elements if e.kind == ElementKind.PORT]
        assert len(ports) == 6
        assert len(spacers) == 1

    def test_group_column_ordering(self):
        yaml = """
version: 1
canvas:
  columns: 5
  rows: 1
views:
  front:
    rows:
      - group:
          spacer: 1
          sections:
            - sequence:
                kind: port
                prefix: "p"
                start: 1
                count: 2
                pattern: all
            - sequence:
                kind: port
                prefix: "p"
                start: 3
                count: 2
                pattern: all
"""
        layout = _parse(yaml)
        ports = sorted(
            [e for e in layout.front.elements if e.kind == ElementKind.PORT],
            key=lambda e: e.col,
        )
        spacers = [e for e in layout.front.elements if e.kind == ElementKind.SPACER]
        # ports at cols 1,2; spacer at col 3; ports at cols 4,5
        assert ports[0].col == 1
        assert ports[1].col == 2
        assert spacers[0].col == 3
        assert ports[2].col == 4
        assert ports[3].col == 5


# ---------------------------------------------------------------------------
# copy_from
# ---------------------------------------------------------------------------


class TestCopyFrom:
    def test_copy_from_front_to_rear(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 1
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "p"
          start: 1
          count: 4
          pattern: all
  rear:
    copy_from: front
"""
        layout = _parse(yaml)
        assert layout.has_separate_faces()
        front_keys = {e.key for e in layout.front.elements}
        rear_keys = {e.key for e in layout.rear.elements}
        assert front_keys == rear_keys

    def test_copy_from_nonexistent_raises(self):
        yaml = """
version: 1
views:
  rear:
    copy_from: nonexistent
"""
        with pytest.raises(LayoutParseError, match="copy_from"):
            _parse(yaml)


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------


class TestVariants:
    YAML = """
version: 1
canvas:
  columns: 14
  rows: 2
views:
  front:
    rows:
      - sequence:
          kind: interface
          prefix: "gi0-"
          start: 1
          count: 6
          pattern: top-odd

variants:
  NM-8X:
    match: module
    rows:
      - sequence:
          kind: interface
          prefix: "te1-"
          start: 1
          count: 8
          pattern: top-odd
"""

    def setup_method(self):
        self.layout = _parse(self.YAML)

    def test_base_elements_present(self):
        assert len(self.layout.front.elements) == 6

    def test_variant_registered(self):
        assert "NM-8X" in self.layout.front.variants

    def test_variant_elements_count(self):
        assert len(self.layout.front.variants["NM-8X"]) == 8

    def test_elements_for_variant_includes_both(self):
        all_els = self.layout.front.elements_for_variant("NM-8X")
        keys = {e.key for e in all_els}
        assert "gi0-1" in keys
        assert "te1-1" in keys


# ---------------------------------------------------------------------------
# Flat elements (explicit at:/span: coordinates)
# ---------------------------------------------------------------------------


class TestFlatElements:
    def test_flat_elements_placed(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 2
views:
  front:
    elements:
      - kind: port
        key: "p1"
        at: {row: 1, col: 1}
      - kind: port
        key: "p2"
        at: {row: 2, col: 1}
      - kind: spacer
        at: {row: 1, col: 2}
        span: {rows: 2, cols: 1}
"""
        layout = _parse(yaml)
        els = {e.key: e for e in layout.front.elements}
        assert "p1" in els
        assert "p2" in els
        assert els["p1"].row == 1
        assert els["p2"].row == 2

    def test_flat_port_missing_key_raises(self):
        yaml = """
version: 1
views:
  front:
    elements:
      - kind: port
        at: {row: 1, col: 1}
"""
        with pytest.raises(LayoutParseError, match="must have a 'key'"):
            _parse(yaml)

    def test_unknown_kind_raises(self):
        yaml = """
version: 1
views:
  front:
    elements:
      - kind: fluxcapacitor
        key: "foo"
"""
        with pytest.raises(LayoutParseError, match="Unknown element kind"):
            _parse(yaml)


# ---------------------------------------------------------------------------
# validate() helper
# ---------------------------------------------------------------------------


_COERCING_HELPERS = {
    "mapping",
    "optional_mapping",
    "named_mapping",
    "items",
    "css_classes",
}
# Uses each key that lenient parsing reads; add each new key here.
_FULL_LAYOUT = {
    "version": 1,
    "meta": {"description": "d", "background": "#000"},
    "canvas": {"columns": 8, "rows": 2, "cell_size": 20, "background": "#111"},
    "views": {
        "front": {
            "background": "#222",
            "canvas": {"columns": 8},
            "rows": [
                {
                    "elements": [
                        "a",
                        {
                            "key": "b",
                            "kind": "port",
                            "label": "B",
                            "face": "front",
                            "css_classes": ["c"],
                            "at": {"row": 1, "col": 2},
                            "span": {"rows": 1, "cols": 1},
                        },
                    ]
                },
                {
                    "sequence": {
                        "kind": "port",
                        "prefix": "p",
                        "start": 1,
                        "count": 2,
                        "step": 1,
                        "pattern": "all",
                        "row": 2,
                    }
                },
                {
                    "group": {
                        "spacer": 1,
                        "sections": [
                            {"sequence": {"prefix": "g", "pattern": "top-odd"}},
                            {"elements": ["e"]},
                            {"blank": 1},
                        ],
                    }
                },
                {"spacer": 1},
                {"blank": 1},
            ],
        },
        "rear": {"copy_from": "front", "elements": [{"key": "r"}]},
    },
    "variants": {
        "V": {"match": "module", "view": "front", "rows": [{"elements": ["v"]}]},
        "W": {"view": "rear", "elements": [{"key": "w"}]},
    },
}


def _paths(node, path=()):
    """Yield the path of each value below ``node``."""
    children = node.items() if isinstance(node, dict) else enumerate(node)
    for key, child in children:
        yield path + (key,)
        if isinstance(child, (dict, list)):
            yield from _paths(child, path + (key,))


def _value_at(path):
    value = _FULL_LAYOUT
    for key in path:
        value = value[key]
    return value


def _yaml_with(path, value):
    data = copy.deepcopy(_FULL_LAYOUT)
    parent = data
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return pyyaml.safe_dump(data)


_ALL_PATHS = list(_paths(_FULL_LAYOUT))
_LIST_PATHS = [p for p in _ALL_PATHS if isinstance(_value_at(p), list)]
_MAPPING_PATHS = [
    ("meta",),
    ("canvas",),
    ("views",),
    ("views", "front"),
    ("views", "front", "canvas"),
    ("views", "rear"),
    ("variants",),
    ("variants", "V"),
    ("variants", "W"),
    ("views", "front", "rows", 0, "elements", 1, "at"),
    ("views", "front", "rows", 0, "elements", 1, "span"),
]
_WRONG_VALUES = ["x", 5, None, [1], {"k": 1}]


def _path_id(path):
    return ".".join(map(str, path))


class TestValidate:
    def test_valid_returns_empty_list(self):
        yaml = "version: 1\nviews: {}"
        errors = validate(yaml)
        assert errors == []

    def test_invalid_returns_errors(self):
        errors = validate("version: 99\nviews: {}")
        assert len(errors) > 0
        assert any("Unsupported" in e for e in errors)

    def test_empty_view_variant_and_copy_source_parse_as_empty(self):
        yaml = """
version: 1
views:
  front:
  rear:
    copy_from: front
variants:
  NM-1:
"""
        assert validate(yaml) == []
        layout = _parse(yaml)
        assert layout.front.elements == []
        assert layout.rear.elements == []
        assert layout.front.variants == {"NM-1": []}

    @pytest.mark.parametrize("path", _MAPPING_PATHS, ids=_path_id)
    @pytest.mark.parametrize(
        "value", [v for v in _WRONG_VALUES if not isinstance(v, dict)], ids=repr
    )
    def test_non_mapping_value_is_read_as_empty(self, path, value):
        assert validate(_yaml_with(path, value)) == []

    def test_full_layout_is_valid(self):
        assert validate(pyyaml.safe_dump(_FULL_LAYOUT)) == []

    @pytest.mark.parametrize("path", _ALL_PATHS, ids=_path_id)
    @pytest.mark.parametrize("value", _WRONG_VALUES, ids=repr)
    def test_wrong_typed_value_gives_a_layout_or_a_parse_error(self, path, value):
        assert not [
            e
            for e in validate(_yaml_with(path, value))
            if e.startswith("Unexpected error")
        ]
        try:
            layout = parse(_yaml_with(path, value))
        except LayoutParseError:
            return
        render_css(layout)

    @pytest.mark.parametrize("value", [5, True], ids=repr)
    def test_non_string_element_key_is_a_parse_error(self, value):
        path = ("views", "rear", "elements", 0, "key")
        with pytest.raises(LayoutParseError, match="key: must be a string"):
            parse(_yaml_with(path, value))

    def test_non_string_view_name_is_a_front_view(self):
        layout = parse("version: 1\nviews:\n  5:\n    elements: [{key: a}]\n")
        assert layout.views[5].face == Face.FRONT
        render_css(layout)

    @pytest.mark.parametrize("path", _LIST_PATHS, ids=_path_id)
    @pytest.mark.parametrize(
        "value", [v for v in _WRONG_VALUES if not isinstance(v, list)], ids=repr
    )
    def test_non_list_value_is_read_as_empty(self, path, value):
        assert parse(_yaml_with(path, value)) == parse(_yaml_with(path, []))

    @pytest.mark.parametrize(
        "path",
        [
            ("views", "front", "rows", 1, "sequence", "kind"),
            ("variants", "V", "view"),
            ("views", "rear", "copy_from"),
        ],
        ids=_path_id,
    )
    @pytest.mark.parametrize("value", [[1], {"k": 1}], ids=repr)
    def test_list_or_mapping_text_value_is_a_parse_error(self, path, value):
        with pytest.raises(LayoutParseError, match="must be a string|must be one of"):
            parse(_yaml_with(path, value))

    def test_coercing_helper_results_are_used(self):
        # A discarded result leaves the raw value, which can be None or a string.
        tree = ast.parse(Path(parser.__file__).read_text())
        discarded = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr in _COERCING_HELPERS
        ]
        assert discarded == []

    def test_bad_yaml_returns_errors(self):
        errors = validate("key: [unclosed")
        assert len(errors) > 0
