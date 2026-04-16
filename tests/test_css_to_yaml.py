"""
Tests for netbox_device_view.management.commands.css_to_yaml

Pure unit tests — no Django, no DB, no NetBox required.
Tests focus on the _grid_to_elements() converter function and its ability to
detect horizontal col_span (double-wide ports like QSFP/100G) as well as
the existing vertical row_span behaviour.
"""

from __future__ import annotations

import pytest

from netbox_device_view.management.commands.css_to_yaml import (
    _classify,
    _css_to_yaml_dict,
    _grid_to_elements,
    _parse_rows,
)


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------


class TestClassify:
    def test_blank_x(self):
        assert _classify("x") == "blank"

    def test_blank_y(self):
        assert _classify("y") == "blank"

    def test_spacer(self):
        assert _classify("s1") == "spacer"

    def test_spacer_multidigit(self):
        assert _classify("s12") == "spacer"

    def test_console(self):
        assert _classify("con0") == "console-port"

    def test_gigabitethernet(self):
        assert _classify("gigabitethernet0-1") == "interface"

    def test_hundredgige(self):
        assert _classify("hundredgige0-25") == "interface"

    def test_twentyfivegige(self):
        assert _classify("twentyfivegige0-1") == "interface"

    def test_unknown_token_falls_back_to_port(self):
        assert _classify("some-unknown-port") == "port"


# ---------------------------------------------------------------------------
# _parse_rows
# ---------------------------------------------------------------------------


class TestParseRows:
    def test_single_row(self):
        css = 'grid-template-areas: "a b c";'
        assert _parse_rows(css) == [["a", "b", "c"]]

    def test_two_rows(self):
        css = 'grid-template-areas: "a b" "c d";'
        assert _parse_rows(css) == [["a", "b"], ["c", "d"]]

    def test_no_grid_template_areas(self):
        css = "color: red;"
        assert _parse_rows(css) == []


# ---------------------------------------------------------------------------
# _grid_to_elements — vertical spanning (existing behaviour)
# ---------------------------------------------------------------------------


class TestVerticalSpan:
    def test_same_token_both_rows_spans_vertically(self):
        """A token in the same column of both rows → span: {rows: 2}."""
        grid = [["x", "a"], ["x", "b"]]
        elements = _grid_to_elements(grid, canvas_rows=2, canvas_cols=2)
        x_els = [e for e in elements if e.get("kind") == "blank"]
        assert len(x_els) == 1
        assert x_els[0]["span"] == {"rows": 2}
        assert x_els[0]["at"] == {"row": 1, "col": 1}

    def test_different_tokens_do_not_span(self):
        """Different tokens in the same column of each row → two separate elements."""
        grid = [["a", "c"], ["b", "d"]]
        elements = _grid_to_elements(grid, canvas_rows=2, canvas_cols=2)
        assert len(elements) == 4  # all four cells separate
        assert not any("span" in e for e in elements)


# ---------------------------------------------------------------------------
# _grid_to_elements — horizontal spanning (new behaviour)
# ---------------------------------------------------------------------------


class TestHorizontalSpan:
    def test_repeated_token_same_row_produces_col_span(self):
        """Two adjacent identical tokens in one row → single element with span: {cols: 2}."""
        grid = [["a", "a"]]
        elements = _grid_to_elements(grid, canvas_rows=1, canvas_cols=2)
        assert len(elements) == 1
        assert elements[0]["key"] == "a"
        assert elements[0]["span"] == {"cols": 2}
        assert elements[0]["at"] == {"row": 1, "col": 1}

    def test_three_repeated_tokens_produces_col_span_3(self):
        grid = [["qsfp-1", "qsfp-1", "qsfp-1"]]
        elements = _grid_to_elements(grid, canvas_rows=1, canvas_cols=3)
        assert len(elements) == 1
        assert elements[0]["span"] == {"cols": 3}

    def test_non_repeated_tokens_stay_single_width(self):
        grid = [["a", "b", "c"]]
        elements = _grid_to_elements(grid, canvas_rows=1, canvas_cols=3)
        assert len(elements) == 3
        assert not any("span" in e for e in elements)

    def test_c9500_style_hundredgig_row1_and_row2(self):
        """
        Mirrors the C9500-24Y4C CSS pattern:
          row 1: ... hundredgige0-25 hundredgige0-25 hundredgige0-27 hundredgige0-27
          row 2: ... hundredgige0-26 hundredgige0-26 hundredgige0-28 hundredgige0-28
        Each pair must produce a single element with span: {cols: 2}.
        """
        grid = [
            ["hundredgige0-25", "hundredgige0-25", "hundredgige0-27", "hundredgige0-27"],
            ["hundredgige0-26", "hundredgige0-26", "hundredgige0-28", "hundredgige0-28"],
        ]
        elements = _grid_to_elements(grid, canvas_rows=2, canvas_cols=4)
        assert len(elements) == 4, f"Expected 4 elements, got {len(elements)}: {elements}"
        keys = {e["key"] for e in elements}
        assert keys == {"hundredgige0-25", "hundredgige0-26", "hundredgige0-27", "hundredgige0-28"}
        for e in elements:
            assert e.get("span") == {"cols": 2}, f"Element {e['key']} missing span: {e}"

    def test_horizontal_span_positions(self):
        """Verify the at: coordinates of each element are correct."""
        grid = [
            ["hundredgige0-25", "hundredgige0-25", "hundredgige0-27", "hundredgige0-27"],
            ["hundredgige0-26", "hundredgige0-26", "hundredgige0-28", "hundredgige0-28"],
        ]
        elements = _grid_to_elements(grid, canvas_rows=2, canvas_cols=4)
        by_key = {e["key"]: e for e in elements}
        assert by_key["hundredgige0-25"]["at"] == {"row": 1, "col": 1}
        assert by_key["hundredgige0-27"]["at"] == {"row": 1, "col": 3}
        assert by_key["hundredgige0-26"]["at"] == {"row": 2, "col": 1}
        assert by_key["hundredgige0-28"]["at"] == {"row": 2, "col": 3}


# ---------------------------------------------------------------------------
# _grid_to_elements — combined horizontal + vertical spanning
# ---------------------------------------------------------------------------


class TestCombinedSpan:
    def test_block_spans_both_rows_and_cols(self):
        """
        A 2×2 block of the same token:
          row 1: a a
          row 2: a a
        → single element with span: {rows: 2, cols: 2}.
        """
        grid = [["a", "a"], ["a", "a"]]
        elements = _grid_to_elements(grid, canvas_rows=2, canvas_cols=2)
        assert len(elements) == 1
        assert elements[0]["span"] == {"rows": 2, "cols": 2}

    def test_partial_block_not_merged(self):
        """
        row 1: a a
        row 2: a b   ← second col differs
        → row 1 gets span: {cols: 2}, row 2 gets two separate elements.
        """
        grid = [["a", "a"], ["a", "b"]]
        elements = _grid_to_elements(grid, canvas_rows=2, canvas_cols=2)
        # row-1 'a' is 2 wide but row-2 'a b' breaks the block → no vertical merge
        # row-1: one element with cols=2; row-2: two elements
        assert len(elements) == 3
        row1_els = [e for e in elements if e["at"]["row"] == 1]
        assert len(row1_els) == 1
        assert row1_els[0]["span"] == {"cols": 2}


# ---------------------------------------------------------------------------
# _css_to_yaml_dict — end-to-end CSS → YAML dict
# ---------------------------------------------------------------------------


class TestCssToyamlEndToEnd:
    def test_c9500_css_produces_double_wide_100g_ports(self):
        """
        Full CSS → YAML conversion for the C9500-24Y4C must produce
        hundredgige0-25/26/27/28 each with span: {cols: 2}.
        """
        css = """\
.deviceview.area {
    grid-template-areas:
        "gigabitethernet-0 x twentyfivegige0-1 twentyfivegige0-3 s1 s1 hundredgige0-25 hundredgige0-25 hundredgige0-27 hundredgige0-27"
        "con0 x twentyfivegige0-2 twentyfivegige0-4 s1 s1 hundredgige0-26 hundredgige0-26 hundredgige0-28 hundredgige0-28";
}
"""
        result = _css_to_yaml_dict(css)
        front_elements = result["views"]["front"]["elements"]
        by_key = {e.get("key"): e for e in front_elements if "key" in e}

        for port in ("hundredgige0-25", "hundredgige0-26", "hundredgige0-27", "hundredgige0-28"):
            assert port in by_key, f"Port {port} missing from converted elements"
            assert by_key[port].get("span", {}).get("cols") == 2, (
                f"Port {port} should have span.cols=2, got: {by_key[port]}"
            )

    def test_vertical_span_still_works_after_fix(self):
        """
        Blank token 'x' in same column of both rows still produces span: {rows: 2}.
        """
        css = """\
.deviceview.area {
    grid-template-areas:
        "x a b"
        "x c d";
}
"""
        result = _css_to_yaml_dict(css)
        front_elements = result["views"]["front"]["elements"]
        blank_els = [e for e in front_elements if e.get("kind") == "blank"]
        assert len(blank_els) == 1
        assert blank_els[0]["span"] == {"rows": 2}

    def test_simple_single_row_no_spans(self):
        """Simple single-row layout with unique tokens → no span attributes."""
        css = """\
.deviceview.area {
    grid-template-areas: "gigabitethernet0-1 gigabitethernet0-2 gigabitethernet0-3";
}
"""
        result = _css_to_yaml_dict(css)
        front_elements = result["views"]["front"]["elements"]
        assert all("span" not in e for e in front_elements)
        assert len(front_elements) == 3

    def test_canvas_dimensions_inferred(self):
        """Canvas columns and rows are inferred from the largest grid."""
        css = """\
.deviceview.area {
    grid-template-areas:
        "a b c d"
        "e f g h";
}
"""
        result = _css_to_yaml_dict(css)
        assert result["canvas"]["columns"] == 4
        assert result["canvas"]["rows"] == 2
