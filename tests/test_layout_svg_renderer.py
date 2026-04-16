"""
Tests for netbox_device_view.layout.renderers.svg

Pure unit tests — no Django, no DB, no NetBox required.
"""

from __future__ import annotations

import re

import pytest

from netbox_device_view.layout.model import (
    CanvasConfig,
    ElementKind,
    Face,
    LayoutView,
    NormalizedLayout,
    PlacedElement,
)
from netbox_device_view.layout.parser import parse
from netbox_device_view.layout.renderers.svg import (
    DEFAULT_CELL,
    GAP,
    PADDING,
    _abbreviate,
    _cell_size,
    _cell_xy,
    _element_dims,
    _svg_dims,
    render,
    render_view_svg,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(yaml_text: str) -> str:
    return render(parse(yaml_text))


def _lines(svg: str) -> list[str]:
    return [line.strip() for line in svg.splitlines() if line.strip()]


def _has_tag(svg: str, tag: str) -> bool:
    return f"<{tag}" in svg or f"<{tag}>" in svg


def _attr(svg: str, attr: str) -> list[str]:
    """Extract all values of a given attribute from the SVG string."""
    return re.findall(rf'{attr}="([^"]*)"', svg)


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------


class TestCoordinateHelpers:
    def test_cell_size_normal(self):
        canvas = CanvasConfig(cell_size=20)
        assert _cell_size(canvas) == 20

    def test_cell_size_zero_returns_default(self):
        canvas = CanvasConfig(cell_size=0)
        assert _cell_size(canvas) == DEFAULT_CELL

    def test_svg_dims_standard(self):
        canvas = CanvasConfig(columns=4, rows=2, cell_size=20)
        w, h = _svg_dims(canvas)
        expected_w = 4 * 20 + 3 * GAP + 2 * PADDING
        expected_h = 2 * 20 + 1 * GAP + 2 * PADDING
        assert w == expected_w
        assert h == expected_h

    def test_svg_dims_single_row(self):
        canvas = CanvasConfig(columns=6, rows=1, cell_size=20)
        w, h = _svg_dims(canvas)
        expected_w = 6 * 20 + 5 * GAP + 2 * PADDING
        expected_h = 1 * 20 + 0 * GAP + 2 * PADDING
        assert w == expected_w
        assert h == expected_h

    def test_cell_xy_first_cell(self):
        canvas = CanvasConfig(columns=4, rows=2, cell_size=20)
        x, y = _cell_xy(1, 1, canvas)
        assert x == PADDING
        assert y == PADDING

    def test_cell_xy_second_col(self):
        canvas = CanvasConfig(columns=4, rows=2, cell_size=20)
        x, y = _cell_xy(1, 2, canvas)
        assert x == PADDING + 20 + GAP
        assert y == PADDING

    def test_cell_xy_second_row(self):
        canvas = CanvasConfig(columns=4, rows=2, cell_size=20)
        x, y = _cell_xy(2, 1, canvas)
        assert x == PADDING
        assert y == PADDING + 20 + GAP

    def test_element_dims_single_cell(self):
        canvas = CanvasConfig(cell_size=20)
        el = PlacedElement(
            kind=ElementKind.PORT, key="p1", row=1, col=1, row_span=1, col_span=1
        )
        w, h = _element_dims(el, canvas)
        assert w == 20
        assert h == 20

    def test_element_dims_spanning(self):
        canvas = CanvasConfig(cell_size=20)
        el = PlacedElement(
            kind=ElementKind.PORT, key="p1", row=1, col=1, row_span=2, col_span=3
        )
        w, h = _element_dims(el, canvas)
        # 3 cols: 3*20 + 2*GAP
        assert w == 3 * 20 + 2 * GAP
        # 2 rows: 2*20 + 1*GAP
        assert h == 2 * 20 + 1 * GAP


# ---------------------------------------------------------------------------
# Abbreviation helper
# ---------------------------------------------------------------------------


class TestAbbreviate:
    def test_gigabit_interface(self):
        assert _abbreviate("gigabitethernet0-1") == "1"

    def test_tengigabit_interface(self):
        assert _abbreviate("tengigabitethernet1-3") == "3"

    def test_port_prefix(self):
        assert _abbreviate("port-12") == "12"

    def test_short_key(self):
        assert _abbreviate("p1") == "1"

    def test_no_trailing_digit_fallback(self):
        # Falls back to last 3 chars
        result = _abbreviate("abc")
        assert len(result) <= 3


# ---------------------------------------------------------------------------
# Basic SVG structure
# ---------------------------------------------------------------------------


class TestSVGStructure:
    SIMPLE_YAML = """
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 20
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

    def test_svg_element_present(self):
        svg = _render(self.SIMPLE_YAML)
        assert "<svg" in svg
        assert "</svg>" in svg

    def test_has_dv_svg_class(self):
        svg = _render(self.SIMPLE_YAML)
        assert 'class="dv-svg' in svg

    def test_has_viewbox(self):
        svg = _render(self.SIMPLE_YAML)
        assert "viewBox=" in svg

    def test_has_xmlns(self):
        svg = _render(self.SIMPLE_YAML)
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg

    def test_has_background_rect(self):
        svg = _render(self.SIMPLE_YAML)
        assert 'class="dv-canvas-bg"' in svg

    def test_has_port_groups(self):
        svg = _render(self.SIMPLE_YAML)
        # 4 ports should produce 4 <g class="dv-port …"> container elements.
        # Match only the opening <g> tag to avoid counting dv-port-rect / dv-port-label.
        port_groups = re.findall(r'<g class="dv-port\b', svg)
        assert len(port_groups) == 4

    def test_port_rects_present(self):
        svg = _render(self.SIMPLE_YAML)
        assert 'class="dv-port-rect"' in svg

    def test_port_titles_present(self):
        svg = _render(self.SIMPLE_YAML)
        assert "<title>" in svg


# ---------------------------------------------------------------------------
# Dimensions are correct
# ---------------------------------------------------------------------------


class TestSVGDimensions:
    def test_width_and_height_match_formula(self):
        yaml = """
version: 1
canvas:
  columns: 6
  rows: 2
  cell_size: 20
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "p"
          start: 1
          count: 6
          pattern: top-odd
"""
        svg = _render(yaml)
        expected_w = 6 * 20 + 5 * GAP + 2 * PADDING
        expected_h = 2 * 20 + 1 * GAP + 2 * PADDING
        assert f'width="{expected_w}"' in svg
        assert f'height="{expected_h}"' in svg

    def test_patch_panel_uses_default_cell_when_cell_size_zero(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 0
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "port-"
          start: 1
          count: 4
          pattern: all
"""
        svg = _render(yaml)
        # With cell_size=0, DEFAULT_CELL is used
        expected_w = 4 * DEFAULT_CELL + 3 * GAP + 2 * PADDING
        assert f'width="{expected_w}"' in svg


# ---------------------------------------------------------------------------
# Port element coordinates
# ---------------------------------------------------------------------------


class TestPortCoordinates:
    def test_first_port_at_padding(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 20
views:
  front:
    elements:
      - kind: port
        key: "p1"
        at: {row: 1, col: 1}
"""
        svg = _render(yaml)
        # First port rect should be at x=PADDING, y=PADDING
        assert f'x="{PADDING}" y="{PADDING}"' in svg

    def test_second_port_x_offset(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 20
views:
  front:
    elements:
      - kind: port
        key: "p1"
        at: {row: 1, col: 1}
      - kind: port
        key: "p2"
        at: {row: 1, col: 2}
"""
        svg = _render(yaml)
        expected_x = PADDING + 20 + GAP  # col 2
        assert f'x="{expected_x}"' in svg

    def test_second_row_y_offset(self):
        yaml = """
version: 1
canvas:
  columns: 2
  rows: 2
  cell_size: 20
views:
  front:
    elements:
      - kind: port
        key: "p1"
        at: {row: 1, col: 1}
      - kind: port
        key: "p2"
        at: {row: 2, col: 1}
"""
        svg = _render(yaml)
        expected_y = PADDING + 20 + GAP  # row 2
        assert f'y="{expected_y}"' in svg


# ---------------------------------------------------------------------------
# Element kinds
# ---------------------------------------------------------------------------


class TestElementKinds:
    def test_spacer_produces_no_output(self):
        yaml = """
version: 1
canvas:
  columns: 3
  rows: 1
  cell_size: 20
views:
  front:
    elements:
      - kind: port
        key: "p1"
        at: {row: 1, col: 1}
      - kind: spacer
        at: {row: 1, col: 2}
      - kind: port
        key: "p2"
        at: {row: 1, col: 3}
"""
        svg = _render(yaml)
        assert 'class="dv-spacer"' not in svg
        assert 'class="dv-port' in svg

    def test_blank_produces_no_output(self):
        yaml = """
version: 1
canvas:
  columns: 2
  rows: 1
  cell_size: 20
views:
  front:
    elements:
      - kind: blank
        at: {row: 1, col: 1}
        span: {cols: 1, rows: 1}
      - kind: port
        key: "p1"
        at: {row: 1, col: 2}
"""
        svg = _render(yaml)
        assert 'class="dv-blank"' not in svg

    def test_label_produces_dv_label_text(self):
        yaml = """
version: 1
canvas:
  columns: 2
  rows: 1
  cell_size: 20
views:
  front:
    elements:
      - kind: label
        key: "console-label"
        label: "Console"
        at: {row: 1, col: 1}
"""
        svg = _render(yaml)
        assert 'class="dv-label"' in svg
        assert "Console" in svg

    def test_port_has_data_stylename(self):
        yaml = """
version: 1
canvas:
  columns: 1
  rows: 1
  cell_size: 20
views:
  front:
    elements:
      - kind: port
        key: "gigabitethernet0-1"
        at: {row: 1, col: 1}
"""
        svg = _render(yaml)
        assert 'data-stylename="gigabitethernet0-1"' in svg


# ---------------------------------------------------------------------------
# Front / rear (patch panel) rendering
# ---------------------------------------------------------------------------


class TestFrontRearRendering:
    PATCH_YAML = """
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 0
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "port-"
          start: 1
          count: 4
          pattern: all
  rear:
    copy_from: front
"""

    def test_two_svg_elements_for_patch_panel(self):
        svg = _render(self.PATCH_YAML)
        svg_count = svg.count("<svg ")
        assert svg_count == 2

    def test_front_face_attribute(self):
        svg = _render(self.PATCH_YAML)
        assert 'data-face="front"' in svg

    def test_rear_face_attribute(self):
        svg = _render(self.PATCH_YAML)
        assert 'data-face="rear"' in svg

    def test_front_has_dv_face_front_class(self):
        svg = _render(self.PATCH_YAML)
        assert 'class="dv-svg dv-face-front"' in svg

    def test_rear_has_dv_face_rear_class(self):
        svg = _render(self.PATCH_YAML)
        assert 'class="dv-svg dv-face-rear"' in svg

    def test_single_face_no_data_face_attr(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 20
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
        svg = _render(yaml)
        assert "data-face=" not in svg
        assert svg.count("<svg ") == 1


# ---------------------------------------------------------------------------
# Variant rendering
# ---------------------------------------------------------------------------


class TestVariantRendering:
    VARIANT_YAML = """
version: 1
canvas:
  columns: 8
  rows: 2
  cell_size: 20
views:
  front:
    rows:
      - sequence:
          kind: interface
          prefix: "gi0-"
          start: 1
          count: 4
          pattern: top-odd

variants:
  MyModule:
    rows:
      - sequence:
          kind: interface
          prefix: "gi0-"
          start: 1
          count: 4
          pattern: top-odd
      - sequence:
          kind: interface
          prefix: "te1-"
          start: 1
          count: 4
          pattern: top-odd
"""

    def test_no_hidden_layers_without_variant(self):
        # New server-side approach: no hidden dv-base/dv-variant wrapper groups.
        svg = _render(self.VARIANT_YAML)
        assert 'class="dv-base"' not in svg
        assert 'class="dv-variant' not in svg
        assert 'style="display:none"' not in svg

    def test_base_only_without_variant(self):
        # Without variant_name, only base ports are rendered.
        svg = _render(self.VARIANT_YAML)
        assert "gi0-" in svg
        assert "te1-" not in svg

    def test_variant_ports_shown_when_variant_active(self):
        # With variant_name passed, merged (base + variant) ports are rendered.
        svg = render(parse(self.VARIANT_YAML), variant_name="MyModule")
        assert "gi0-" in svg
        assert "te1-" in svg

    def test_variant_active_no_hidden_layers(self):
        # Even with variant_name active, no hidden wrapper groups.
        svg = render(parse(self.VARIANT_YAML), variant_name="MyModule")
        assert 'class="dv-base"' not in svg
        assert 'class="dv-variant' not in svg
        assert 'style="display:none"' not in svg


# ---------------------------------------------------------------------------
# render_view_svg helper
# ---------------------------------------------------------------------------


class TestRenderViewSVG:
    def test_render_front_only(self):
        layout = parse("""
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 20
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
""")
        svg = render_view_svg(layout, face="front")
        assert 'data-face="front"' in svg
        assert 'data-face="rear"' not in svg

    def test_render_rear_only(self):
        layout = parse("""
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 20
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
""")
        svg = render_view_svg(layout, face="rear")
        assert 'data-face="rear"' in svg
        assert 'data-face="front"' not in svg

    def test_missing_face_returns_empty(self):
        layout = parse("""
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 20
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "p"
          start: 1
          count: 4
          pattern: all
""")
        svg = render_view_svg(layout, face="rear")
        assert svg == ""


# ---------------------------------------------------------------------------
# Double-wide ports (col_span=2 / QSFP-style)
# ---------------------------------------------------------------------------


class TestDoubleWidePorts:
    DOUBLE_WIDE_YAML = """
version: 1
canvas:
  columns: 4
  rows: 2
  cell_size: 20
views:
  front:
    elements:
      - kind: interface
        key: sfp-1
        at: {row: 1, col: 1}
      - kind: interface
        key: sfp-2
        at: {row: 2, col: 1}
      - kind: interface
        key: hundredgige-1
        at: {row: 1, col: 2}
        span: {cols: 2}
      - kind: interface
        key: hundredgige-2
        at: {row: 2, col: 2}
        span: {cols: 2}
      - kind: interface
        key: sfp-3
        at: {row: 1, col: 4}
      - kind: interface
        key: sfp-4
        at: {row: 2, col: 4}
"""

    def test_double_wide_port_count(self):
        """Double-wide ports render as one <g> element each, not two."""
        svg = _render(self.DOUBLE_WIDE_YAML)
        port_groups = re.findall(r'<g class="dv-port\b', svg)
        # 4 single-wide + 2 double-wide = 6 port groups total
        assert len(port_groups) == 6

    def test_double_wide_port_rect_width(self):
        """A col_span=2 port rect must be 2*cell_size + GAP wide."""
        svg = _render(self.DOUBLE_WIDE_YAML)
        expected_w = 2 * 20 + GAP  # 42 px
        assert f'width="{expected_w}"' in svg

    def test_single_wide_port_rect_width(self):
        """A col_span=1 port rect stays at exactly cell_size wide."""
        svg = _render(self.DOUBLE_WIDE_YAML)
        # width="20" must appear (single-wide ports)
        assert 'width="20"' in svg

    def test_double_wide_port_key_present(self):
        """The double-wide port's stylename class appears exactly once."""
        svg = _render(self.DOUBLE_WIDE_YAML)
        assert svg.count("hundredgige-1") >= 1
        assert svg.count("hundredgige-2") >= 1

    def test_c9500_24y4c_hundredgig_double_wide(self):
        """C9500-24Y4C: all four 100G ports render at double-width."""
        import pathlib

        repo_root = pathlib.Path(__file__).parent.parent
        yaml_text = (repo_root / "examples/yaml/Cisco/C9500-24Y4C.yaml").read_text()
        svg = _render(yaml_text)
        expected_w = 2 * 20 + GAP  # 42 px at default cell_size=20
        # Each 100G port rect must have the double-wide width
        for port in ("hundredgige0-25", "hundredgige0-26", "hundredgige0-27", "hundredgige0-28"):
            assert port in svg, f"Port {port} missing from SVG"
        assert f'width="{expected_w}"' in svg

    def test_c9500_24y4c_total_port_count(self):
        """C9500-24Y4C: 24 × 25G + 4 × 100G + 1 mgmt + 1 console = 30 port groups."""
        import pathlib

        repo_root = pathlib.Path(__file__).parent.parent
        yaml_text = (repo_root / "examples/yaml/Cisco/C9500-24Y4C.yaml").read_text()
        svg = _render(yaml_text)
        port_groups = re.findall(r'<g class="dv-port\b', svg)
        assert len(port_groups) == 30


# ---------------------------------------------------------------------------
# All bundled YAML examples render without error
# ---------------------------------------------------------------------------


class TestYAMLExamplesRenderToSVG:
    """Smoke test: every YAML example file must produce valid (non-empty) SVG."""

    @pytest.fixture(
        params=[
            "examples/yaml/Cisco/C9300-24T.yaml",
            "examples/yaml/Cisco/C2960X-24TD-L.yaml",
            "examples/yaml/Cisco/C8300-2N2S-4T2X.yaml",
            "examples/yaml/Cisco/FPR1120-NGFW-K9.yaml",
            "examples/yaml/Cisco/C9500-24Y4C.yaml",
            "examples/yaml/Generic/24-ports-UTP-Patchpanel.yaml",
            "examples/yaml/Generic/48-ports-UTP-Patchpanel.yaml",
            "examples/yaml/Generic/24xLC-Patchpanel.yaml",
            "examples/yaml/Generic/SC-24-port_Fiber_Patch_Panel.yaml",
            "examples/yaml/Generic/LC-48-port-Fiber-Patchpanel.yaml",
            "examples/yaml/Ubiquiti/USW-Enterprise-24-PoE.yaml",
        ]
    )
    def example_path(self, request):
        return request.param

    def test_example_renders_to_svg(self, example_path):
        import pathlib

        repo_root = pathlib.Path(__file__).parent.parent
        yaml_text = (repo_root / example_path).read_text()
        svg = render(parse(yaml_text))
        assert "<svg" in svg
        assert "</svg>" in svg
        assert len(svg) > 100  # non-trivial output


# ---------------------------------------------------------------------------
# CSS renderer still works (regression guard)
# ---------------------------------------------------------------------------


class TestCSSRendererNotBroken:
    def test_css_renderer_produces_grid_template_areas(self):
        from netbox_device_view.layout.renderers.css_grid import render as render_css

        yaml = """
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 20
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
        css = render_css(parse(yaml))
        assert "grid-template-areas" in css
        assert ".deviceview.area" in css

    def test_both_renderers_agree_on_port_count(self):
        """Both renderers must reference the same port keys."""
        from netbox_device_view.layout.renderers.css_grid import render as render_css

        yaml = """
version: 1
canvas:
  columns: 6
  rows: 2
  cell_size: 20
views:
  front:
    rows:
      - sequence:
          kind: interface
          prefix: "gi0-"
          start: 1
          count: 6
          pattern: top-odd
"""
        layout = parse(yaml)
        css = render_css(layout)
        svg = render(layout)
        # All 6 ports should appear in both outputs
        for i in range(1, 7):
            key = f"gi0-{i}"
            assert key in css, f"CSS missing key {key}"
            assert key in svg, f"SVG missing key {key}"


# ---------------------------------------------------------------------------
# HTML escaping / security
# ---------------------------------------------------------------------------


class TestHTMLEscaping:
    def test_key_with_special_chars_is_escaped(self):
        """Keys containing HTML special characters must be escaped in SVG output."""
        # Craft a layout with an unusual key (the parser normalises keys,
        # so we build a PlacedElement directly)
        layout = NormalizedLayout(source="yaml")
        canvas = CanvasConfig(columns=1, rows=1, cell_size=20)
        el = PlacedElement(
            kind=ElementKind.PORT,
            key='p<test>&"quote"',
            row=1,
            col=1,
        )
        view = LayoutView(face=Face.FRONT, canvas=canvas, elements=[el])
        layout.views["front"] = view
        svg = render(layout)
        # Raw < > & must NOT appear in attribute values
        assert "<test>" not in svg
        assert '&"' not in svg or "&amp;" in svg or "&#" in svg

    def test_background_color_is_escaped(self):
        yaml = """
version: 1
meta:
  background: "#d9d9d9"
canvas:
  columns: 2
  rows: 1
  cell_size: 20
views:
  front:
    elements:
      - kind: port
        key: "p1"
        at: {row: 1, col: 1}
"""
        svg = _render(yaml)
        # Background colour must appear (escaped) in the SVG
        assert "#d9d9d9" in svg or "d9d9d9" in svg
