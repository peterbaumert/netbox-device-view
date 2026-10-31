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

# The CSS import profile accepts these selector classes and sizing properties.
GRID_CLASSES = ("deviceview", "area")
FRONT_CLASS = "dFront"
REAR_CLASS = "dRear"
MODULE_CLASS_PREFIX = "module"
AUTO_SIZING_PROPERTIES = ("grid-auto-rows", "grid-auto-columns")
_GRID_SELECTOR = "".join(f".{name}" for name in GRID_CLASSES)


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
            parts.append(_render_view(layout.front, selector_suffix=f".{FRONT_CLASS}"))
        if layout.rear:
            parts.append(_render_view(layout.rear, selector_suffix=f".{REAR_CLASS}"))
    else:
        # Single-face device (switch, router, firewall)
        view = layout.front or layout.rear
        if view:
            parts.append(_render_view(view, selector_suffix=""))
            # Render variants for this view
            for variant_name in view.variants:
                parts.append(_render_variant(view, variant_name))

    return "\n".join(parts)


# ── Private helpers ────────────────────────────────────────────────────────────


def _render_view(view: LayoutView, selector_suffix: str) -> str:
    """Render a single LayoutView to a CSS block."""
    selector = f"{_GRID_SELECTOR}{selector_suffix}"
    grid_areas = _elements_to_grid_template_areas(view.elements, view.canvas)
    return _build_css_block(selector, view.canvas, grid_areas)


def _render_variant(base_view: LayoutView, variant_name: str) -> str:
    """Render a module variant overlay CSS block."""
    deviceview, area = GRID_CLASSES
    selector = f".{deviceview}.{MODULE_CLASS_PREFIX}{variant_name}.{area}"
    grid_areas = _elements_to_grid_template_areas(
        base_view.elements_for_variant(variant_name), base_view.canvas
    )
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
        lines.extend(f"    {name}: auto;" for name in AUTO_SIZING_PROPERTIES)

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
    """
    return [" ".join(row) for row in area_grid(elements, canvas)]


def area_grid(elements: list[PlacedElement], canvas: CanvasConfig) -> list[list[str]]:
    """Return the area name of each canvas cell, or "." for an empty cell."""
    rows = canvas.rows
    cols = canvas.columns

    # grid[row][col] = area name ("." = empty)
    grid: list[list[str]] = [["." for _ in range(cols)] for _ in range(rows)]

    # Sort elements to place in a predictable order; a later element paints over an earlier one
    sorted_elements = sorted(elements, key=lambda e: (e.row, e.col))

    for el in sorted_elements:
        row_range, col_range = painted_area(el, canvas)
        for r in row_range:
            for c in col_range:
                grid[r][c] = el.key

    return grid


def non_rectangular_area(rows: list[list[str]]) -> str | None:
    """Return the first named area whose cells do not fill one rectangle, or None."""
    # Browsers drop the whole grid-template-areas declaration for such an area.
    cells: dict[str, list[tuple[int, int]]] = {}
    for r, row in enumerate(rows):
        for c, name in enumerate(row):
            if name.strip("."):
                cells.setdefault(name, []).append((r, c))
    for name, spots in cells.items():
        height = max(r for r, _ in spots) - min(r for r, _ in spots) + 1
        width = max(c for _, c in spots) - min(c for _, c in spots) + 1
        if len(spots) != height * width:
            return name
    return None


def painted_area(el: PlacedElement, canvas: CanvasConfig) -> tuple[range, range]:
    """Return the 0-based rows and columns that ``el`` paints, clamped to ``canvas``."""
    rows = range(max(el.row - 1, 0), min(el.row - 1 + el.row_span, canvas.rows))
    cols = range(max(el.col - 1, 0), min(el.col - 1 + el.col_span, canvas.columns))
    return rows, cols


def _is_patch_panel_canvas(canvas: CanvasConfig) -> bool:
    """
    Heuristic: if the canvas rows == 1 or auto sizing was requested
    (indicated by cell_size == 0, a sentinel), treat as patch-panel.

    In practice we detect patch panels by the face selector (.dFront/.dRear)
    rather than the canvas config, but this provides a fallback.
    """
    return canvas.cell_size == 0
