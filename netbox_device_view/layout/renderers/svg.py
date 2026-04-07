"""
SVG renderer.

Converts a NormalizedLayout (from either the YAML parser or the legacy adapter)
into a self-contained SVG document that can be embedded directly in a Django
template or served as a standalone file.

Rendering path
--------------
  NormalizedLayout
      └─ LayoutView  (front / rear)
              └─ PlacedElement[]    →   <g class="dv-port …"> <rect> <text> </g>
              └─ variants           →   <g class="dv-variant-<Name>"> …

Coordinate system
-----------------
Each grid cell is ``canvas.cell_size`` pixels square (default 20 px).  A
``PADDING`` margin is added around the grid.  The full SVG viewport is:

    width  = columns * cell_size + 2 * PADDING
    height = rows    * cell_size + (rows - 1) * GAP + 2 * PADDING

``GAP`` (2 px) matches the ``grid-gap`` used by the CSS Grid renderer so the
two renderings look visually equivalent.

Element classes
---------------
Port/interface elements receive the following CSS classes so that existing
JavaScript and NetBox Bootstrap themes can target them identically to the
CSS Grid elements:

    <g class="dv-port {stylename}" data-stylename="{stylename}" …>

Variant elements are additionally wrapped in a ``<g class="dv-variant-{Name}">``
container that is hidden by default; a module class on the outer SVG unhides it
(mirrors the CSS ``.deviceview.module{Name}`` selector pattern).

Interactivity
-------------
- ``<title>`` on each port group provides native SVG hover tooltip.
- ``data-bs-toggle="tooltip"`` and ``data-bs-html="true"`` are added so
  Bootstrap tooltips activate on SVG elements the same way they do on ``<a>``
  tags in the CSS layout.
- Port groups have ``tabindex="0"`` for keyboard navigation.
- Status/cable-color classes are added in the template layer (not here) via
  JavaScript after the SVG is embedded; the SVG renderer only emits the
  structural skeleton.

Face/variant rendering
----------------------
Single-face devices produce a single ``<svg>`` element with a ``data-face``
attribute.  Dual-face devices (patch panels) produce two sibling ``<svg>``
elements, one per face, each with ``data-face="front"`` or ``data-face="rear"``.

Variants are rendered as hidden ``<g class="dv-variant-{Name}">`` blocks inside
the base view SVG.  When the NetBox template detects a matching installed module
it adds ``class="dv-active-variant-{Name}"`` to the outer ``<svg>`` which a
small CSS rule uses to show/hide variant layers.

Label elements
--------------
LABEL elements produce a ``<text>`` element only (no rect), centred in their
cell.  Useful for chassis labels (e.g. "Console", "Mgmt").

Spacer / blank elements
-----------------------
These produce a ``<rect class="dv-spacer">`` or ``<rect class="dv-blank">``
with no label and no interactivity.
"""

from __future__ import annotations

import html
from typing import Optional

from ..model import (
    CanvasConfig,
    ElementKind,
    LayoutView,
    NormalizedLayout,
    PlacedElement,
)

# ── Layout constants ───────────────────────────────────────────────────────────

PADDING = 6  # px around the grid inside the SVG viewport
GAP = 2  # px between cells (matches CSS grid-gap)
DEFAULT_CELL = 32  # px — used when canvas.cell_size is 0 (patch-panel auto)
CORNER_RADIUS = 3  # px — border-radius for port rects
FONT_SIZE = 7  # px — port label font size
LABEL_FONT_SIZE = 9  # px — standalone label element font size

# Colour palette for structural elements (ports get status colours in templates)
COLOUR_BACKGROUND = "#d9d9d9"  # default canvas background
COLOUR_PORT_DEFAULT = "#6c757d"  # fallback port fill (Bootstrap secondary)
COLOUR_BORDER = "#cccccc"  # port stroke
COLOUR_LABEL_TEXT = "#ffffff"  # text inside port rects
COLOUR_STANDALONE_TEXT = "#333"  # text for label elements


# ── Public API ─────────────────────────────────────────────────────────────────


def render(layout: NormalizedLayout, variant_name: Optional[str] = None) -> str:
    """
    Render a NormalizedLayout to one or more SVG strings.

    Returns a single string containing either one or two ``<svg>`` elements
    depending on whether the device has separate front/rear faces.

    Parameters
    ----------
    layout:
        The normalized layout produced by the YAML parser or legacy adapter.
    variant_name:
        If given, render this variant overlay on top of the base elements
        (exactly as the CSS renderer does for module overlays).
        Pass ``None`` to render the base layout only.

    Returns
    -------
    str
        Raw SVG markup ready for embedding in a Django template with
        ``{% autoescape off %} {{ svg }} {% endautoescape %}``.
    """
    parts: list[str] = []

    if layout.has_separate_faces():
        if layout.front:
            parts.append(
                _render_view(layout.front, data_face="front", variant_name=variant_name)
            )
        if layout.rear:
            parts.append(
                _render_view(layout.rear, data_face="rear", variant_name=variant_name)
            )
    else:
        view = layout.front or layout.rear
        if view:
            parts.append(_render_view(view, data_face=None, variant_name=variant_name))

    return "\n".join(parts)


def render_view_svg(
    layout: NormalizedLayout,
    face: str = "front",
    variant_name: Optional[str] = None,
) -> str:
    """
    Render only one face of a layout to SVG.

    Convenience wrapper used by the template layer when it wants to render
    front and rear separately (e.g. to place them in different tab panes).

    Parameters
    ----------
    layout:
        The normalized layout.
    face:
        ``"front"`` or ``"rear"``.
    variant_name:
        Optional module variant to activate.
    """
    view = layout.views.get(face)
    if view is None:
        view = layout.front if face == "front" else layout.rear
    if view is None:
        return ""
    return _render_view(view, data_face=face, variant_name=variant_name)


# ── Coordinate helpers ─────────────────────────────────────────────────────────


def _cell_size(canvas: CanvasConfig) -> int:
    """Return effective cell size, replacing the patch-panel sentinel (0) with DEFAULT_CELL."""
    return canvas.cell_size if canvas.cell_size > 0 else DEFAULT_CELL


def _svg_dims(canvas: CanvasConfig) -> tuple[int, int]:
    """Return (total_width, total_height) for the SVG viewport."""
    cs = _cell_size(canvas)
    w = canvas.columns * cs + max(0, canvas.columns - 1) * GAP + 2 * PADDING
    h = canvas.rows * cs + max(0, canvas.rows - 1) * GAP + 2 * PADDING
    return w, h


def _cell_xy(row: int, col: int, canvas: CanvasConfig) -> tuple[int, int]:
    """Convert 1-based (row, col) grid coords to SVG pixel coords (top-left of cell)."""
    cs = _cell_size(canvas)
    x = PADDING + (col - 1) * (cs + GAP)
    y = PADDING + (row - 1) * (cs + GAP)
    return x, y


def _element_dims(el: PlacedElement, canvas: CanvasConfig) -> tuple[int, int]:
    """Return (width, height) in pixels for an element, including its span."""
    cs = _cell_size(canvas)
    w = el.col_span * cs + max(0, el.col_span - 1) * GAP
    h = el.row_span * cs + max(0, el.row_span - 1) * GAP
    return w, h


# ── Private rendering helpers ──────────────────────────────────────────────────


def _render_view(
    view: LayoutView,
    data_face: Optional[str],
    variant_name: Optional[str],
) -> str:
    """Render a single LayoutView to an ``<svg>`` element string."""
    canvas = view.canvas
    width, height = _svg_dims(canvas)
    bg = canvas.background or COLOUR_BACKGROUND

    face_attr = f' data-face="{data_face}"' if data_face else ""
    # CSS classes on the outer SVG mirror the `.deviceview.area.dFront` etc. pattern.
    face_class = f" dv-face-{data_face}" if data_face else ""

    lines: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' class="dv-svg{face_class}"'
        f' width="{width}" height="{height}"'
        f' viewBox="0 0 {width} {height}"'
        f"{face_attr}"
        f' role="img" aria-label="Device layout{" — " + data_face if data_face else ""}">',
        "  <defs>",
        '    <pattern id="dv-nocolor-pattern" patternUnits="userSpaceOnUse" width="10" height="10" patternTransform="rotate(-45)">',
        '      <rect width="5" height="10" fill="grey"/>',
        '      <rect x="5" width="5" height="10" fill="black"/>',
        "    </pattern>",
        "  </defs>",
    ]

    # Background rect
    lines.append(
        f'  <rect class="dv-canvas-bg" x="0" y="0" width="{width}" height="{height}"'
        f' rx="5" ry="5" fill="{html.escape(bg)}" stroke="#999" stroke-width="1"/>'
    )

    # Render the correct element set for the requested variant (or base if None).
    # elements_for_variant merges base + variant overrides — no hidden layers needed.
    elements = view.elements_for_variant(variant_name)
    lines.extend(_render_elements(elements, canvas))

    lines.append("</svg>")
    return "\n".join(lines)


def _render_elements(
    elements: list[PlacedElement],
    canvas: CanvasConfig,
    group_class: str = "",
    indent: int = 2,
) -> list[str]:
    """Render a list of PlacedElement objects to SVG lines."""
    pad = " " * indent
    lines: list[str] = []
    for el in sorted(elements, key=lambda e: (e.row, e.col)):
        lines.extend(_render_element(el, canvas, pad))
    return lines


def _render_element(
    el: PlacedElement, canvas: CanvasConfig, pad: str = "  "
) -> list[str]:
    """Render a single PlacedElement to SVG lines."""
    x, y = _cell_xy(el.row, el.col, canvas)
    w, h = _element_dims(el, canvas)
    cs = _cell_size(canvas)

    if el.kind == ElementKind.LABEL:
        return _render_label_element(el, x, y, w, h, pad)
    if el.kind == ElementKind.SPACER:
        return _render_spacer_element(el, x, y, w, h, pad)
    if el.kind == ElementKind.BLANK:
        return _render_blank_element(el, x, y, w, h, pad)

    # Port / interface / console-port / power-port / module-slot
    return _render_port_element(el, x, y, w, h, cs, pad)


def _render_port_element(
    el: PlacedElement,
    x: int,
    y: int,
    w: int,
    h: int,
    cs: int,
    pad: str,
) -> list[str]:
    """
    Render a port/interface element.

    The ``<g>`` element carries the stylename as a class so templates can
    locate it via ``document.querySelector('.dv-port.{stylename}')``.
    Status classes (bg-success, bg-secondary, bg-danger) and cable colors
    are applied in the template layer, not here.
    """
    key = el.key
    safe_key = html.escape(key)
    label = el.label or _abbreviate(key)
    safe_label = html.escape(label)

    extra_classes = " ".join(el.css_classes)
    g_class = f"dv-port {safe_key}{' ' + extra_classes if extra_classes else ''}"

    # Port rect — fill is a CSS variable so templates can override per-port
    cx = x + w // 2
    cy = y + h // 2

    lines = [
        f'{pad}<g class="{g_class}" data-stylename="{safe_key}"'
        f' tabindex="0" role="button" aria-label="{safe_key}">',
        f"{pad}  <title>{safe_key}</title>",
        f'{pad}  <rect class="dv-port-rect" x="{x}" y="{y}" width="{w}" height="{h}"'
        f' rx="{CORNER_RADIUS}" ry="{CORNER_RADIUS}"'
        f' fill="{COLOUR_PORT_DEFAULT}" stroke="{COLOUR_BORDER}" stroke-width="1"/>',
    ]

    # Only show text if the cell is wide or tall enough to fit it
    if w >= 10 and h >= 8:
        lines.append(
            f'{pad}  <text class="dv-port-label" x="{cx}" y="{cy}"'
            f' text-anchor="middle" dominant-baseline="central"'
            f' font-size="{FONT_SIZE}" fill="{COLOUR_LABEL_TEXT}"'
            f' pointer-events="none">{safe_label}</text>'
        )

    lines.append(f"{pad}</g>")
    return lines


def _render_spacer_element(
    el: PlacedElement,
    x: int,
    y: int,
    w: int,
    h: int,
    pad: str,
) -> list[str]:
    # Spacers are invisible column separators — no SVG output needed.
    # The coordinate space is already reserved by the grid calculation,
    # so port groups naturally have a visible gap between them.
    return []


def _render_blank_element(
    el: PlacedElement,
    x: int,
    y: int,
    w: int,
    h: int,
    pad: str,
) -> list[str]:
    # Blanks are transparent in SVG — empty panel sections show through
    # to the canvas background without a separate rect element.
    return []


def _render_label_element(
    el: PlacedElement,
    x: int,
    y: int,
    w: int,
    h: int,
    pad: str,
) -> list[str]:
    text = html.escape(el.label or el.key)
    cx = x + w // 2
    cy = y + h // 2
    return [
        f'{pad}<text class="dv-label" x="{cx}" y="{cy}"'
        f' text-anchor="middle" dominant-baseline="central"'
        f' font-size="{LABEL_FONT_SIZE}" fill="{COLOUR_STANDALONE_TEXT}">{text}</text>',
    ]


# ── Text abbreviation helper ───────────────────────────────────────────────────


def _abbreviate(key: str) -> str:
    """
    Produce a short display label from a port key.

    Examples:
        "gigabitethernet0-1"  →  "1"
        "tengigabitethernet1-3" → "3"
        "port-12"             → "12"
        "console-1"           → "co1"
    """
    # Strip common long prefixes, keep trailing number
    import re

    # Try to extract trailing integer(s)
    m = re.search(r"(\d+)(?:[^a-zA-Z\d]*(\d+))?$", key)
    if m:
        if m.group(2):
            return m.group(2)
        return m.group(1)
    # Fallback: keep last 3 characters
    return key[-3:] if len(key) > 3 else key
