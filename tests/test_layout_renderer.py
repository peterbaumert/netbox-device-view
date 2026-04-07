"""
Tests for netbox_device_view.layout.renderers.css_grid

These are pure unit tests — no Django, no DB, no NetBox required.
"""

from netbox_device_view.layout.parser import parse
from netbox_device_view.layout.renderers.css_grid import render

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(yaml_text: str) -> str:
    return render(parse(yaml_text))


def _lines(css: str) -> list[str]:
    return [line.strip() for line in css.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Selector conventions
# ---------------------------------------------------------------------------


class TestSelectors:
    def test_single_face_uses_plain_area_selector(self):
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
"""
        css = _render(yaml)
        assert ".deviceview.area {" in css
        assert ".dFront" not in css
        assert ".dRear" not in css

    def test_patch_panel_uses_dFront_dRear_selectors(self):
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
        css = _render(yaml)
        assert ".deviceview.area.dFront {" in css
        assert ".deviceview.area.dRear {" in css

    def test_variant_selector(self):
        yaml = """
version: 1
canvas:
  columns: 8
  rows: 2
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
  NM-4X:
    match: module
    rows:
      - sequence:
          kind: interface
          prefix: "te1-"
          start: 1
          count: 4
          pattern: top-odd
"""
        css = _render(yaml)
        assert ".deviceview.moduleNM-4X.area {" in css


# ---------------------------------------------------------------------------
# grid-template-areas content
# ---------------------------------------------------------------------------


class TestGridTemplateAreas:
    def test_single_row_all_ports_present(self):
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
"""
        css = _render(yaml)
        assert "p1" in css
        assert "p2" in css
        assert "p3" in css
        assert "p4" in css

    def test_two_row_top_odd_pattern_row_structure(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 2
views:
  front:
    rows:
      - sequence:
          kind: interface
          prefix: "gi-"
          start: 1
          count: 4
          pattern: top-odd
"""
        css = _render(yaml)
        # Extract the two grid-template-areas rows
        in_areas = False
        area_rows = []
        for line in css.splitlines():
            stripped = line.strip()
            if "grid-template-areas" in stripped:
                in_areas = True
                continue
            if in_areas and stripped.startswith('"'):
                area_rows.append(stripped.strip('"').strip(";").strip())
            elif in_areas and stripped == "}":
                break

        assert len(area_rows) == 2
        row1, row2 = area_rows
        # Odd ports (gi-1, gi-3) should appear in row 1
        assert "gi-1" in row1
        assert "gi-3" in row1
        # Even ports (gi-2, gi-4) should appear in row 2
        assert "gi-2" in row2
        assert "gi-4" in row2

    def test_spacer_appears_in_output(self):
        yaml = """
version: 1
canvas:
  columns: 3
  rows: 1
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "p"
          start: 1
          count: 2
          pattern: all
      - spacer: 1
"""
        css = _render(yaml)
        # A spacer key (s-something) should appear in the areas
        assert "grid-template-areas" in css
        # At least one cell that is not a port key
        lines_with_areas = [
            line for line in css.splitlines() if line.strip().startswith('"')
        ]
        row_content = " ".join(lines_with_areas)
        cells = row_content.replace('"', "").split()
        non_port_cells = [c for c in cells if not c.startswith("p")]
        assert len(non_port_cells) > 0

    def test_blank_fills_cells(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 1
views:
  front:
    rows:
      - blank: 2
      - sequence:
          kind: port
          prefix: "p"
          start: 1
          count: 2
          pattern: all
"""
        css = _render(yaml)
        assert "p1" in css
        assert "p2" in css
        # Blanks fill the first 2 columns — the area row should have 4 cells
        lines_with_areas = [
            line for line in css.splitlines() if line.strip().startswith('"')
        ]
        cells = lines_with_areas[0].strip().strip('"').strip(";").split()
        assert len(cells) == 4


# ---------------------------------------------------------------------------
# Background color
# ---------------------------------------------------------------------------


class TestBackground:
    def test_background_color_emitted(self):
        yaml = """
version: 1
meta:
  background: "#d9d9d9"
canvas:
  columns: 2
  rows: 1
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
        css = _render(yaml)
        assert "background-color: #d9d9d9" in css

    def test_no_background_not_emitted(self):
        yaml = """
version: 1
canvas:
  columns: 2
  rows: 1
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
        css = _render(yaml)
        assert "background-color" not in css


# ---------------------------------------------------------------------------
# Patch-panel auto sizing
# ---------------------------------------------------------------------------


class TestPatchPanelSizing:
    def test_patch_panel_has_grid_auto(self):
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
          prefix: "p"
          start: 1
          count: 4
          pattern: all
  rear:
    copy_from: front
"""
        css = _render(yaml)
        assert "grid-auto-rows: auto" in css
        assert "grid-auto-columns: auto" in css

    def test_standard_switch_no_grid_auto(self):
        yaml = """
version: 1
canvas:
  columns: 4
  rows: 2
views:
  front:
    rows:
      - sequence:
          kind: interface
          prefix: "gi-"
          start: 1
          count: 4
          pattern: top-odd
"""
        css = _render(yaml)
        assert "grid-auto-rows" not in css
        assert "grid-auto-columns" not in css


# ---------------------------------------------------------------------------
# Variant merging
# ---------------------------------------------------------------------------


class TestVariantMerging:
    def test_variant_css_contains_base_and_variant_ports(self):
        """
        When a variant rows block re-specifies the full layout (base + variant
        ports), the rendered variant CSS block contains both port sets.
        This matches the real-world pattern used in C9300-24T with C9300-NM-8X.
        """
        yaml = """
version: 1
canvas:
  columns: 8
  rows: 2
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
  NM-4X:
    match: module
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
        css = _render(yaml)
        # Find the variant block
        variant_start = css.index(".deviceview.moduleNM-4X.area")
        variant_block = css[variant_start:]
        assert "gi0-1" in variant_block
        assert "te1-1" in variant_block

    def test_base_block_does_not_contain_variant_ports(self):
        yaml = """
version: 1
canvas:
  columns: 8
  rows: 2
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
  NM-4X:
    match: module
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
        css = _render(yaml)
        # Base block ends before variant block
        base_end = css.index(".deviceview.moduleNM-4X.area")
        base_block = css[:base_end]
        assert "te1-1" not in base_block


# ---------------------------------------------------------------------------
# End-to-end: C9300-24T CSS equivalence
# ---------------------------------------------------------------------------


class TestC9300EndToEnd:
    """Verify that the YAML for C9300-24T produces CSS with the expected keys."""

    YAML = """
version: 1
meta:
  description: "Cisco Catalyst 9300-24T"
canvas:
  columns: 32
  rows: 2
views:
  front:
    rows:
      - blank: 14
      - group:
          spacer: 1
          sections:
            - sequence:
                kind: interface
                prefix: "gigabitethernet0-"
                start: 1
                count: 12
                pattern: top-odd
            - sequence:
                kind: interface
                prefix: "gigabitethernet0-"
                start: 13
                count: 12
                pattern: top-odd
      - spacer: 4
variants:
  C9300-NM-8X:
    match: module
    rows:
      - blank: 14
      - group:
          spacer: 1
          sections:
            - sequence:
                kind: interface
                prefix: "gigabitethernet0-"
                start: 1
                count: 12
                pattern: top-odd
            - sequence:
                kind: interface
                prefix: "gigabitethernet0-"
                start: 13
                count: 12
                pattern: top-odd
            - sequence:
                kind: interface
                prefix: "tengigabitethernet1-"
                start: 1
                count: 8
                pattern: top-odd
"""

    def setup_method(self):
        self.css = _render(self.YAML)

    def test_base_selector_present(self):
        assert ".deviceview.area {" in self.css

    def test_all_24_ge_ports_in_base(self):
        base_end = self.css.find(".deviceview.moduleC9300-NM-8X.area")
        base = self.css[:base_end] if base_end != -1 else self.css
        for i in range(1, 25):
            assert f"gigabitethernet0-{i}" in base

    def test_variant_selector_present(self):
        assert ".deviceview.moduleC9300-NM-8X.area {" in self.css

    def test_variant_contains_10ge_ports(self):
        variant_start = self.css.index(".deviceview.moduleC9300-NM-8X.area")
        variant_block = self.css[variant_start:]
        for i in range(1, 9):
            assert f"tengigabitethernet1-{i}" in variant_block

    def test_grid_has_correct_column_count(self):
        """Each row of the base block should have exactly 32 cells."""
        in_base = False
        in_areas = False
        area_rows = []
        for line in self.css.splitlines():
            s = line.strip()
            if ".deviceview.area {" in s:
                in_base = True
            if in_base and "moduleC9300" in s:
                break
            if in_base and "grid-template-areas" in s:
                in_areas = True
                continue
            if in_base and in_areas and s.startswith('"'):
                cells = s.strip('"').rstrip(";").strip()
                area_rows.append(cells.split())
        assert len(area_rows) == 2
        assert len(area_rows[0]) == 32
        assert len(area_rows[1]) == 32
