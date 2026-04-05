"""
Tests for netbox_device_view.layout.parser

These are pure unit tests — no Django, no DB, no NetBox required.
"""

import pytest

from netbox_device_view.layout.model import (
    CanvasConfig,
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
# Sequence expansion — pattern: odd (2-row Cisco style)
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
          pattern: odd
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
# Sequence expansion — pattern: even
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
          pattern: even
"""


class TestSequenceEven:
    def setup_method(self):
        self.layout = _parse(EVEN_YAML)
        self.elements = self.layout.front.elements

    def test_even_ports_in_row_1(self):
        # "even" pattern: even-numbered ports go in row 1
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
          pattern: odd

variants:
  NM-8X:
    match: module
    rows:
      - sequence:
          kind: interface
          prefix: "te1-"
          start: 1
          count: 8
          pattern: odd
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


class TestValidate:
    def test_valid_returns_empty_list(self):
        yaml = "version: 1\nviews: {}"
        errors = validate(yaml)
        assert errors == []

    def test_invalid_returns_errors(self):
        errors = validate("version: 99\nviews: {}")
        assert len(errors) > 0
        assert any("Unsupported" in e for e in errors)

    def test_bad_yaml_returns_errors(self):
        errors = validate("key: [unclosed")
        assert len(errors) > 0
