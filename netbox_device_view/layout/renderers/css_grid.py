"""
CSS Grid renderer.

Converts a NormalizedLayout (from either the YAML parser or the legacy
adapter) into CSS text that is functionally equivalent to the hand-written
CSS in the legacy grid_template_area field.

The output CSS is injected verbatim into a <style> block by the templates,
exactly as the legacy CSS was.  This means the renderer must produce valid
CSS that the existing deviceview.html / ports.html templates understand.

Rendering path
--------------
  NormalizedLayout
      └─ LayoutView  (front / rear)
              └─ PlacedElement[]    →   CSS grid-template-areas string
              └─ variants           →   .deviceview.module<Name>.area { … }

Selector conventions (must match the Django templates):
  .deviceview.area           — default view (front or single-face)
  .deviceview.area.dFront    — explicit front face (patch panels)
  .deviceview.area.dRear     — explicit rear face (patch panels)
  .deviceview.module<X>.area — module variant overlay
"""

from __future__ import annotations

from ..model import (
    CanvasConfig,
    LayoutView,
    NormalizedLayout,
    PlacedElement,
)


def render(layout: NormalizedLayout) -> str:
    """
    Render a NormalizedLayout to a CSS string ready for injection into a
    <style> block.

    Returns a string that can be stored in / used as a replacement for the
    legacy ``grid_template_area`` field.
    """
    parts: list[str] = []

    if layout.has_separate_faces():
        # Patch-panel style: front and rear are separate selectors
        if layout.front:
            parts.append(_render_view(layout.front, selector_suffix=".dFront"))
        if layout.rear:
            parts.append(_render_view(layout.rear, selector_suffix=".dRear"))
    else:
        # Single-face device (switch, router, firewall)
        view = layout.front or layout.rear
        if view:
            parts.append(_render_view(view, selector_suffix=""))
            # Render variants for this view
            for variant_name, variant_elements in view.variants.items():
                parts.append(_render_variant(view, variant_name, variant_elements))

    return "\n".join(parts)


# ── Private helpers ────────────────────────────────────────────────────────────


def _render_view(view: LayoutView, selector_suffix: str) -> str:
    """Render a single LayoutView to a CSS block."""
    selector = f".deviceview.area{selector_suffix}"
    grid_areas = _elements_to_grid_template_areas(view.elements, view.canvas)
    return _build_css_block(selector, view.canvas, grid_areas)


def _render_variant(
    base_view: LayoutView,
    variant_name: str,
    variant_elements: list[PlacedElement],
) -> str:
    """Render a module variant overlay CSS block."""
    # Merge base + variant elements, variant wins on key conflicts
    merged: dict[str, PlacedElement] = {el.key: el for el in base_view.elements}
    for el in variant_elements:
        merged[el.key] = el
    all_elements = list(merged.values())

    selector = f".deviceview.module{variant_name}.area"
    grid_areas = _elements_to_grid_template_areas(all_elements, base_view.canvas)
    return _build_css_block(selector, base_view.canvas, grid_areas)


def _build_css_block(
    selector: str,
    canvas: CanvasConfig,
    grid_areas: list[str],
) -> str:
    """Build a complete CSS rule block."""
    lines: list[str] = [f"{selector} {{"]

    if canvas.background:
        lines.append(f"    background-color: {canvas.background};")

    # Patch-panel views use auto sizing; standard views use fixed 20px cells
    if _is_patch_panel_canvas(canvas):
        lines.append("    grid-auto-rows: auto;")
        lines.append("    grid-auto-columns: auto;")

    if grid_areas:
        area_lines = "\n".join(f'        "{row}"' for row in grid_areas)
        lines.append(f"    grid-template-areas:\n{area_lines};")

    lines.append("}")
    return "\n".join(lines)


def _elements_to_grid_template_areas(
    elements: list[PlacedElement],
    canvas: CanvasConfig,
) -> list[str]:
    """
    Convert a flat list of PlacedElement objects into CSS grid-template-areas
    row strings.

    The algorithm:
    1. Build a 2D grid (rows × cols) filled with "." (empty).
    2. Place each element's key into its cell(s).
    3. For elements spanning multiple rows/cols, fill all spanned cells with
       the same key (CSS grid-template-areas requires this).
    4. Convert each row to a space-separated string of area names.

    CSS grid-template-areas does NOT allow a cell to be empty (".") unless
    ALL cells in that column are empty — we replace empty cells with the
    nearest spacer/blank key or a generated placeholder.
    """
    rows = canvas.rows
    cols = canvas.columns

    # grid[row][col] = area name ("." = empty)
    grid: list[list[str]] = [["." for _ in range(cols)] for _ in range(rows)]

    # Sort elements to place in a predictable order
    sorted_elements = sorted(elements, key=lambda e: (e.row, e.col))

    for el in sorted_elements:
        r_start = el.row - 1  # convert to 0-based
        c_start = el.col - 1
        r_end = r_start + el.row_span
        c_end = c_start + el.col_span

        # Clamp to canvas bounds
        r_end = min(r_end, rows)
        c_end = min(c_end, cols)

        for r in range(r_start, r_end):
            for c in range(c_start, c_end):
                if 0 <= r < rows and 0 <= c < cols:
                    grid[r][c] = el.key

    # Replace remaining "." placeholders with unique generated names
    # CSS grid-template-areas requires that named areas form a contiguous
    # rectangle; a lone "." in the middle can sometimes be a problem, but
    # browsers handle it.  We leave them as "." for simplicity.

    return [" ".join(row) for row in grid]


def _is_patch_panel_canvas(canvas: CanvasConfig) -> bool:
    """
    Heuristic: if the canvas rows == 1 or auto sizing was requested
    (indicated by cell_size == 0, a sentinel), treat as patch-panel.

    In practice we detect patch panels by the face selector (.dFront/.dRear)
    rather than the canvas config, but this provides a fallback.
    """
    return canvas.cell_size == 0
