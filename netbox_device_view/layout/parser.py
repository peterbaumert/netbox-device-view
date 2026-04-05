"""
YAML layout parser and compiler.

Parses the YAML-based layout schema and compiles it into a NormalizedLayout.

Schema overview
---------------
version: 1
meta:
  description: "Human-readable name"
  background: "#d9d9d9"    # optional default background for all views

canvas:
  columns: 32              # total grid columns
  rows: 2                  # total grid rows
  cell_size: 20            # px per cell (for future SVG renderer)

views:
  front:                   # or "rear", or both
    background: "#000"     # optional per-view background override
    rows:                  # ordered list of row definitions
      - elements: [...]    # inline element list
      - sequence:          # expand a port sequence
          kind: port
          prefix: "gigabitethernet0-"
          start: 1
          count: 12
          pattern: odd     # "odd" | "even" | "all" (default)
      - spacer: 1          # shorthand: insert N spacer columns
      - blank: 4           # shorthand: insert N blank (x) columns

variants:
  C9300-NM-8X:             # module model name
    match: module           # "module" (matched by installed module model)
    rows:
      - elements: [...]

# Alternatively, views can use the flat "elements" list with explicit at:/span:
views:
  front:
    elements:
      - kind: port
        key: "gigabitethernet0-1"
        at: {row: 1, col: 15}
      - kind: spacer
        at: {row: 1, col: 14}
        span: {rows: 2, cols: 1}
      - kind: blank
        at: {row: 1, col: 1}
        span: {rows: 2, cols: 14}

Sequence helper
---------------
A sequence expands into multiple port elements.  The ``pattern`` key controls
which ports go in which row when the canvas has 2 rows:

  pattern: odd      → ports 1, 3, 5, … in row 1; 2, 4, 6, … in row 2
  pattern: even     → ports 2, 4, 6, … in row 1; 1, 3, 5, … in row 2
  pattern: all      → all ports in a single row (patch panel style)

Group helper
------------
A group is a list of sequences/elements separated by spacers:

  - group:
      spacer: 1
      sections:
        - sequence: ...
        - sequence: ...

copy_from / extend_view
-----------------------
A view can copy another view's row definitions and add overrides:

  views:
    rear:
      copy_from: front
      # additional elements appended / overriding by key
"""

from __future__ import annotations

import re
from typing import Any, Optional

import yaml

from .model import (
    CanvasConfig,
    ElementKind,
    Face,
    LayoutView,
    NormalizedLayout,
    PlacedElement,
)

# ── Schema version supported by this parser ──────────────────────────────────
SUPPORTED_VERSION = 1

# ── Mapping from YAML kind strings to ElementKind enum ───────────────────────
_KIND_MAP: dict[str, ElementKind] = {
    "port": ElementKind.PORT,
    "interface": ElementKind.INTERFACE,
    "console-port": ElementKind.CONSOLE_PORT,
    "console_port": ElementKind.CONSOLE_PORT,
    "power-port": ElementKind.POWER_PORT,
    "power_port": ElementKind.POWER_PORT,
    "module-slot": ElementKind.MODULE_SLOT,
    "module_slot": ElementKind.MODULE_SLOT,
    "spacer": ElementKind.SPACER,
    "blank": ElementKind.BLANK,
    "label": ElementKind.LABEL,
}

_PORT_KINDS = {
    ElementKind.PORT,
    ElementKind.INTERFACE,
    ElementKind.CONSOLE_PORT,
    ElementKind.POWER_PORT,
    ElementKind.MODULE_SLOT,
}


class LayoutParseError(ValueError):
    """Raised when the YAML layout schema is invalid."""


# ── Public API ────────────────────────────────────────────────────────────────


def parse(yaml_text: str) -> NormalizedLayout:
    """
    Parse a YAML layout string and return a NormalizedLayout.

    Raises LayoutParseError on schema violations.
    """
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise LayoutParseError(f"Invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise LayoutParseError("Layout must be a YAML mapping at the top level.")

    version = data.get("version", 1)
    if int(version) != SUPPORTED_VERSION:
        raise LayoutParseError(
            f"Unsupported layout version {version!r}. "
            f"This parser supports version {SUPPORTED_VERSION}."
        )

    meta = data.get("meta", {}) or {}
    description = meta.get("description", "")
    global_bg = meta.get("background", None)

    canvas_cfg = _parse_canvas(data.get("canvas", {}), global_bg)

    # Collect raw view definitions
    raw_views: dict[str, Any] = data.get("views", {}) or {}
    raw_variants: dict[str, Any] = data.get("variants", {}) or {}

    layout = NormalizedLayout(description=description, source="yaml")

    # First pass: compile all views (copy_from resolution happens here)
    compiled_views: dict[str, LayoutView] = {}
    for view_name, view_data in raw_views.items():
        face = _parse_face(view_name)
        view_canvas = _parse_canvas(view_data.get("canvas", {}), global_bg, canvas_cfg)
        compiled_views[view_name] = _compile_view(
            view_name, view_data, face, view_canvas, raw_views, canvas_cfg, global_bg
        )

    # Second pass: compile variants into each affected view
    for variant_name, variant_data in raw_variants.items():
        variant_view_name = variant_data.get("view", "front")
        if variant_view_name not in compiled_views:
            # Create an implicit front view if needed
            compiled_views[variant_view_name] = LayoutView(
                face=Face.FRONT,
                canvas=canvas_cfg,
            )
        view = compiled_views[variant_view_name]
        variant_elements = _compile_rows(
            variant_data.get("rows", []),
            variant_data.get("elements", []),
            view.canvas,
            variant_name=variant_name,
        )
        view.variants[variant_name] = variant_elements

    layout.views = compiled_views
    return layout


def validate(yaml_text: str) -> list[str]:
    """
    Validate a YAML layout and return a list of error messages.
    Returns an empty list if the layout is valid.
    """
    errors: list[str] = []
    try:
        parse(yaml_text)
    except LayoutParseError as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Unexpected error: {exc}")
    return errors


# ── Internal helpers ──────────────────────────────────────────────────────────


def _parse_face(name: str) -> Face:
    mapping = {"front": Face.FRONT, "rear": Face.REAR, "both": Face.BOTH}
    return mapping.get(name.lower(), Face.FRONT)


def _parse_canvas(
    raw: Any,
    global_bg: Optional[str] = None,
    parent: Optional[CanvasConfig] = None,
) -> CanvasConfig:
    """Merge canvas config from raw dict, inheriting from parent if given."""
    if not isinstance(raw, dict):
        raw = {}
    p = parent or CanvasConfig()
    return CanvasConfig(
        columns=int(raw.get("columns", p.columns)),
        rows=int(raw.get("rows", p.rows)),
        cell_size=int(raw.get("cell_size", p.cell_size)),
        background=raw.get("background", global_bg or p.background),
    )


def _compile_view(
    view_name: str,
    view_data: Any,
    face: Face,
    canvas: CanvasConfig,
    raw_views: dict[str, Any],
    global_canvas: CanvasConfig,
    global_bg: Optional[str],
) -> LayoutView:
    """Compile a single view, handling copy_from."""
    if not isinstance(view_data, dict):
        view_data = {}

    # Handle copy_from
    base_elements: list[PlacedElement] = []
    copy_from = view_data.get("copy_from")
    if copy_from:
        if copy_from not in raw_views:
            raise LayoutParseError(
                f"View '{view_name}' references copy_from='{copy_from}' "
                f"which does not exist."
            )
        src = raw_views[copy_from]
        src_canvas = _parse_canvas(src.get("canvas", {}), global_bg, global_canvas)
        base_elements = _compile_rows(
            src.get("rows", []),
            src.get("elements", []),
            src_canvas,
        )

    elements = _compile_rows(
        view_data.get("rows", []),
        view_data.get("elements", []),
        canvas,
    )

    # Merge: base_elements first, then this view's elements override by key
    if base_elements:
        merged: dict[str, PlacedElement] = {el.key: el for el in base_elements}
        for el in elements:
            merged[el.key] = el
        elements = list(merged.values())

    return LayoutView(face=face, canvas=canvas, elements=elements)


def _compile_rows(
    rows: list[Any],
    flat_elements: list[Any],
    canvas: CanvasConfig,
    variant_name: Optional[str] = None,
) -> list[PlacedElement]:
    """
    Compile either a rows-based or flat elements-based definition into
    a list of PlacedElement objects.
    """
    if flat_elements:
        return _compile_flat_elements(flat_elements, canvas, variant_name)
    if rows:
        return _compile_rows_definition(rows, canvas, variant_name)
    return []


def _compile_flat_elements(
    elements: list[Any],
    canvas: CanvasConfig,
    variant_name: Optional[str],
) -> list[PlacedElement]:
    """Compile an explicit list of element dicts with at:/span: coordinates."""
    result: list[PlacedElement] = []
    for i, raw in enumerate(elements):
        if not isinstance(raw, dict):
            raise LayoutParseError(f"Element #{i} must be a mapping, got {type(raw).__name__}.")
        result.extend(_expand_element(raw, canvas, variant_name))
    return result


def _compile_rows_definition(
    rows: list[Any],
    canvas: CanvasConfig,
    variant_name: Optional[str],
) -> list[PlacedElement]:
    """
    Compile a rows-style definition.

    Each entry in ``rows`` can be:
      - {elements: [...]}         explicit list for this row
      - {sequence: {...}}         expanded port sequence
      - {spacer: N}               insert N spacer columns
      - {blank: N}                insert N blank columns
      - {group: {spacer: N, sections: [...]}}  grouped sequences

    Row number is determined by the ``pattern`` of each sequence:
      - "all"  → fills a single row (auto-incremented)
      - "odd"  → odd-numbered ports go in row 1
      - "even" → even-numbered ports go in row 2 (row 1 gets the evens)
    """
    result: list[PlacedElement] = []

    # We build a column cursor per row. row_cursors[1], row_cursors[2], …
    row_cursors: dict[int, int] = {r: 1 for r in range(1, canvas.rows + 1)}
    current_single_row = 1  # for "all"-pattern sequences

    for entry in rows:
        if not isinstance(entry, dict):
            raise LayoutParseError(f"Row entry must be a mapping, got {type(entry).__name__}.")

        if "elements" in entry:
            elements, row_cursors = _compile_explicit_row(
                entry["elements"], row_cursors, current_single_row, canvas, variant_name
            )
            result.extend(elements)
            current_single_row = min(current_single_row + 1, canvas.rows)

        elif "sequence" in entry:
            elements, row_cursors = _expand_sequence(
                entry["sequence"], row_cursors, canvas, variant_name
            )
            result.extend(elements)

        elif "group" in entry:
            elements, row_cursors = _expand_group(
                entry["group"], row_cursors, canvas, variant_name
            )
            result.extend(elements)

        elif "spacer" in entry:
            n = int(entry["spacer"])
            spacer_elements, row_cursors = _insert_filler(
                ElementKind.SPACER, n, row_cursors, canvas, variant_name, prefix="s"
            )
            result.extend(spacer_elements)

        elif "blank" in entry:
            n = int(entry["blank"])
            blank_elements, row_cursors = _insert_filler(
                ElementKind.BLANK, n, row_cursors, canvas, variant_name, prefix="x"
            )
            result.extend(blank_elements)

        else:
            raise LayoutParseError(
                f"Unknown row entry keys: {list(entry.keys())}. "
                "Expected one of: elements, sequence, group, spacer, blank."
            )

    return result


def _compile_explicit_row(
    elements: list[Any],
    row_cursors: dict[int, int],
    current_row: int,
    canvas: CanvasConfig,
    variant_name: Optional[str],
) -> tuple[list[PlacedElement], dict[int, int]]:
    """Place an explicit list of elements into ``current_row``."""
    result: list[PlacedElement] = []
    col = row_cursors.get(current_row, 1)
    for raw in elements:
        if isinstance(raw, str):
            # shorthand: just a key name, infer kind
            kind = ElementKind.SPACER if raw.startswith("s") and raw[1:].isdigit() else ElementKind.BLANK if raw in ("x", "y") else ElementKind.PORT
            result.append(PlacedElement(
                kind=kind, key=raw, row=current_row, col=col, variant=variant_name
            ))
            col += 1
        elif isinstance(raw, dict):
            expanded = _expand_element(raw, canvas, variant_name, default_row=current_row, default_col=col)
            result.extend(expanded)
            col += sum(el.col_span for el in expanded)
        else:
            raise LayoutParseError(f"Element must be a string key or mapping, got {type(raw).__name__}.")
    row_cursors[current_row] = col
    return result, row_cursors


def _expand_sequence(
    seq: Any,
    row_cursors: dict[int, int],
    canvas: CanvasConfig,
    variant_name: Optional[str],
) -> tuple[list[PlacedElement], dict[int, int]]:
    """
    Expand a sequence definition into PlacedElement objects.

    sequence:
      kind: port           # default: port
      prefix: "gigabitethernet0-"
      start: 1             # first port number (default: 1)
      count: 24            # how many ports
      pattern: odd         # "odd" | "even" | "all"
      step: 1              # increment between ports (default: 1)
    """
    if not isinstance(seq, dict):
        raise LayoutParseError("'sequence' must be a mapping.")

    kind_str = seq.get("kind", "port")
    kind = _KIND_MAP.get(kind_str)
    if kind is None:
        raise LayoutParseError(f"Unknown element kind: {kind_str!r}.")

    prefix = seq.get("prefix", "")
    start = int(seq.get("start", 1))
    count = int(seq.get("count", 1))
    step = int(seq.get("step", 1))
    pattern = seq.get("pattern", "odd")

    # Generate port numbers
    port_numbers = [start + i * step for i in range(count)]

    result: list[PlacedElement] = []
    row_cursors = dict(row_cursors)  # copy

    if pattern == "all":
        # All ports in a single row (patch-panel style)
        row = _single_active_row(row_cursors, canvas)
        col = row_cursors.get(row, 1)
        for n in port_numbers:
            key = f"{prefix}{n}"
            result.append(PlacedElement(kind=kind, key=key, row=row, col=col, variant=variant_name))
            col += 1
        row_cursors[row] = col

    elif pattern in ("odd", "even"):
        # Two-row layout: odd ports in row 1, even ports in row 2
        # (or swapped for "even" pattern — useful when even ports are physically on top)
        row1, row2 = (1, 2) if canvas.rows >= 2 else (1, 1)
        if pattern == "even":
            row1, row2 = row2, row1

        # Find the starting column (max of the two row cursors to keep alignment)
        start_col = max(row_cursors.get(row1, 1), row_cursors.get(row2, 1))
        col1 = col2 = start_col

        for n in port_numbers:
            key = f"{prefix}{n}"
            if n % 2 == 1:  # odd
                result.append(PlacedElement(kind=kind, key=key, row=row1, col=col1, variant=variant_name))
                col1 += 1
            else:  # even
                result.append(PlacedElement(kind=kind, key=key, row=row2, col=col2, variant=variant_name))
                col2 += 1

        # Both cursors advance to the same column
        final_col = max(col1, col2)
        row_cursors[row1] = final_col
        row_cursors[row2] = final_col

    else:
        raise LayoutParseError(f"Unknown sequence pattern: {pattern!r}. Use 'odd', 'even', or 'all'.")

    return result, row_cursors


def _expand_group(
    group: Any,
    row_cursors: dict[int, int],
    canvas: CanvasConfig,
    variant_name: Optional[str],
) -> tuple[list[PlacedElement], dict[int, int]]:
    """
    Expand a group of sections separated by spacers.

    group:
      spacer: 1        # spacer width between sections
      sections:
        - sequence: ...
        - sequence: ...
    """
    if not isinstance(group, dict):
        raise LayoutParseError("'group' must be a mapping.")

    spacer_width = int(group.get("spacer", 1))
    sections = group.get("sections", [])
    result: list[PlacedElement] = []

    for i, section in enumerate(sections):
        if i > 0:
            # Insert spacer between sections
            spacer_els, row_cursors = _insert_filler(
                ElementKind.SPACER, spacer_width, row_cursors, canvas, variant_name, prefix="s"
            )
            result.extend(spacer_els)

        if not isinstance(section, dict):
            raise LayoutParseError("Group section must be a mapping.")

        if "sequence" in section:
            els, row_cursors = _expand_sequence(section["sequence"], row_cursors, canvas, variant_name)
            result.extend(els)
        elif "elements" in section:
            row = _single_active_row(row_cursors, canvas)
            els, row_cursors = _compile_explicit_row(section["elements"], row_cursors, row, canvas, variant_name)
            result.extend(els)
        elif "blank" in section:
            n = int(section["blank"])
            els, row_cursors = _insert_filler(ElementKind.BLANK, n, row_cursors, canvas, variant_name, prefix="x")
            result.extend(els)
        else:
            raise LayoutParseError(f"Unknown group section keys: {list(section.keys())}.")

    return result, row_cursors


def _insert_filler(
    kind: ElementKind,
    count: int,
    row_cursors: dict[int, int],
    canvas: CanvasConfig,
    variant_name: Optional[str],
    prefix: str = "x",
) -> tuple[list[PlacedElement], dict[int, int]]:
    """Insert ``count`` filler elements spanning all active rows."""
    result: list[PlacedElement] = []
    row_cursors = dict(row_cursors)
    start_col = max(row_cursors.get(r, 1) for r in range(1, canvas.rows + 1))

    for i in range(count):
        col = start_col + i
        # Generate a unique key that won't clash with port names
        key = f"{prefix}{col}"
        if canvas.rows >= 2:
            # One element spanning all rows
            result.append(PlacedElement(
                kind=kind,
                key=key,
                row=1,
                col=col,
                row_span=canvas.rows,
                col_span=1,
                variant=variant_name,
            ))
        else:
            result.append(PlacedElement(
                kind=kind, key=key, row=1, col=col, variant=variant_name
            ))

    # Advance all row cursors
    new_col = start_col + count
    for r in range(1, canvas.rows + 1):
        row_cursors[r] = new_col

    return result, row_cursors


def _expand_element(
    raw: dict[str, Any],
    canvas: CanvasConfig,
    variant_name: Optional[str],
    default_row: int = 1,
    default_col: int = 1,
) -> list[PlacedElement]:
    """Compile a single explicit element dict."""
    kind_str = raw.get("kind", "port")
    kind = _KIND_MAP.get(kind_str)
    if kind is None:
        raise LayoutParseError(f"Unknown element kind: {kind_str!r}.")

    at = raw.get("at", {}) or {}
    span = raw.get("span", {}) or {}

    row = int(at.get("row", default_row))
    col = int(at.get("col", default_col))
    row_span = int(span.get("rows", 1))
    col_span = int(span.get("cols", 1))

    # key is required for port-like elements; optional for spacers/blanks
    key = raw.get("key", "")
    if not key:
        if kind in _PORT_KINDS:
            raise LayoutParseError(f"Element of kind {kind_str!r} must have a 'key'.")
        # auto-generate for spacers/blanks
        key = f"{'s' if kind == ElementKind.SPACER else 'x'}{col}"

    label = raw.get("label")
    css_classes = raw.get("css_classes", []) or []

    face_str = raw.get("face", "both")
    face_map = {"front": Face.FRONT, "rear": Face.REAR, "both": Face.BOTH}
    face = face_map.get(face_str, Face.BOTH)

    return [PlacedElement(
        kind=kind,
        key=key,
        face=face,
        row=row,
        col=col,
        row_span=row_span,
        col_span=col_span,
        label=label,
        css_classes=list(css_classes),
        variant=variant_name,
    )]


def _single_active_row(row_cursors: dict[int, int], canvas: CanvasConfig) -> int:
    """Return the first row number (1) for single-row operations."""
    return 1
