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
          pattern: top-odd  # "top-odd" | "top-even" | "all" (default: top-odd)
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

  pattern: top-odd  → ports 1, 3, 5, … in row 1; 2, 4, 6, … in row 2
  pattern: top-even → ports 2, 4, 6, … in row 1; 1, 3, 5, … in row 2
  pattern: all      → all ports in a single row (patch panel style)
                      optional ``row: 2`` places them in the bottom row instead of row 1

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

import itertools
import re
from collections import Counter
from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any

import tinycss2
import tinycss2.color4
import yaml

from .model import (
    CanvasConfig,
    ElementKind,
    Face,
    LayoutView,
    NormalizedLayout,
    PlacedElement,
    replace_by_key,
)
from .renderers.css_grid import area_grid, non_rectangular_area, painted_area

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


# ── Strict rules (parse(..., strict=True), used by the Data Source import) ────
MAX_CANVAS_CELLS = 4096
MAX_CANVAS_ROWS = 64
MAX_EXPANDED_ELEMENTS = 10000
# Every view and variant renders its whole canvas grid again.
MAX_RENDERED_CELLS = 4 * MAX_CANVAS_CELLS
# Each element costs its painted cells, at least one, in every render that includes it.
MAX_PAINTED_CELLS = 4 * MAX_RENDERED_CELLS
MAX_CELL_SIZE = 100
MAX_LABEL_LENGTH = 64
MAX_KEY_LENGTH = 64
MAX_CSS_CLASSES = 8
_MAX_YAML_DEPTH = 32
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
# Appended to "module" in a CSS class, so a leading digit is valid.
VARIANT_NAME = re.compile(r"[A-Za-z0-9_-]+")
_VIEW_NAMES = ("front", "rear")
_FACES = {face.value: face for face in Face}
_ROW_ENTRY_KEYS = ("elements", "sequence", "group", "spacer", "blank")
_SECTION_KEYS = ("sequence", "elements", "blank")
_KEYS = {
    "layout": {"version", "meta", "canvas", "views", "variants"},
    "meta": {"description", "background"},
    "canvas": {"columns", "rows", "cell_size", "background"},
    "view": {"background", "canvas", "copy_from", "rows", "elements"},
    "variant": {"match", "view", "rows", "elements"},
    "sequence": {"kind", "prefix", "start", "count", "pattern", "step", "row"},
    "group": {"spacer", "sections"},
    "element": {"kind", "key", "at", "span", "label", "css_classes", "face"},
    "at": {"row", "col"},
    "span": {"rows", "cols"},
}


class LayoutParseError(ValueError):
    """Raised when the YAML layout schema is invalid."""


_MERGE_TAG = "tag:yaml.org,2002:merge"
_MERGE_KEY = object()


class _DepthLimitedLoader(yaml.SafeLoader):
    """A SafeLoader that stops at deep nesting before the scanner and composer slow down."""

    depth = 0

    def compose_node(self, parent, index):
        is_alias = self.check_event(yaml.AliasEvent)
        self.depth += 1
        if self.depth > _MAX_YAML_DEPTH:
            raise LayoutParseError("Invalid YAML: the document is nested too deeply.")
        try:
            node = super().compose_node(parent, index)
        finally:
            self.depth -= 1
        if not is_alias and isinstance(node, yaml.MappingNode):
            self._reject_duplicate_keys(node)
        return node

    def _reject_duplicate_keys(self, node):
        # Check the composed node: construction flattens merge keys in place.
        seen = set()
        for key_node, _ in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                continue
            if key_node.tag == _MERGE_TAG:
                key, name = _MERGE_KEY, "<<"
            else:
                # deep=True makes a container tag on a scalar key fail here.
                key = self.construct_object(key_node, deep=True)
                name = repr(key)
            if key in seen:
                raise LayoutParseError(f"Invalid YAML: duplicate key {name:.80}.")
            seen.add(key)


def one_css_color(tokens: list[Any]) -> list[Any] | None:
    """Return the token list of one CSS color without whitespace, or None."""
    values = [token for token in tokens if token.type != "whitespace"]
    if len(values) == 1 and tinycss2.color4.parse_color(values[0]) is not None:
        return values
    return None


def _is_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_KEY_LENGTH
        and IDENTIFIER.fullmatch(value) is not None
    )


@dataclass
class _Rules:
    """The strict import rules; with strict=False each check keeps the lenient behaviour."""

    strict: bool = False
    elements_left: int = MAX_EXPANDED_ELEMENTS
    cells_left: int = MAX_RENDERED_CELLS

    def optional_mapping(self, raw: Any, kind: str, where: str) -> dict[str, Any]:
        return self.mapping({} if raw is None else raw, kind, where)

    def named_mapping(self, raw: Any, where: str) -> dict[Any, Any]:
        """Return a mapping whose keys are names, such as views or variants."""
        if isinstance(raw, dict):
            return raw
        if self.strict and raw is not None:
            raise LayoutParseError(f"{where}: must be a mapping.")
        return {}

    def mapping(self, raw: Any, kind: str, where: str) -> dict[str, Any]:
        """Return ``raw`` as a mapping; lenient mode reads a non-mapping as empty."""
        if not self.strict:
            return raw if isinstance(raw, dict) else {}
        if not isinstance(raw, dict):
            raise LayoutParseError(f"{where}: must be a mapping.")
        unknown = sorted(str(key) for key in raw if key not in _KEYS[kind])
        if unknown:
            raise LayoutParseError(f"{where}: unknown key {', '.join(unknown)}.")
        return raw

    def one_of(self, raw: Any, keys: tuple[str, ...], where: str) -> None:
        if self.strict and (
            not isinstance(raw, dict) or len(raw) != 1 or next(iter(raw)) not in keys
        ):
            raise LayoutParseError(
                f"{where}: must have exactly one key of {', '.join(keys)}."
            )

    def items(self, raw: Any, where: str) -> list[Any]:
        """Return ``raw`` as a list; lenient mode reads a non-list as empty."""
        if isinstance(raw, list):
            return raw
        if self.strict:
            raise LayoutParseError(f"{where}: must be a list.")
        return []

    def integer(
        self, raw: Any, where: str, minimum: int = 0, maximum: int | None = None
    ) -> int:
        if not self.strict:
            try:
                return int(raw)
            except (TypeError, ValueError, OverflowError) as exc:
                raise LayoutParseError(f"{where}: must be an integer.") from exc
        if (
            type(raw) is not int
            or raw < minimum
            or (maximum is not None and raw > maximum)
        ):
            bounds = (
                f"{minimum} or more"
                if maximum is None
                else f"from {minimum} to {maximum}"
            )
            raise LayoutParseError(f"{where}: must be an integer {bounds}.")
        return raw

    def text(self, raw: Any, where: str, choices: tuple[str, ...] = ()) -> Any:
        """Return ``raw``; lenient mode rejects only an unhashable value, such as a list."""
        if self.strict:
            invalid = not isinstance(raw, str) or bool(choices and raw not in choices)
        else:
            invalid = not isinstance(raw, Hashable)
        if invalid:
            expected = f"one of {', '.join(choices)}" if choices else "a string"
            raise LayoutParseError(f"{where}: must be {expected}.")
        return raw

    def background(self, raw: Any, where: str) -> Any:
        """Return a strict background as tinycss2 serializes its one color value."""
        if not self.strict or raw is None:
            return raw
        color = None
        if isinstance(raw, str):
            color = one_css_color(tinycss2.parse_component_value_list(raw))
        if color is None:
            raise LayoutParseError(f"{where}: background must be one CSS color.")
        return tinycss2.serialize(color)

    def css_classes(self, raw: Any, where: str) -> list[Any]:
        """Return ``raw`` as a list; lenient mode reads a non-list as empty."""
        if not self.strict:
            return self.items(raw, where)
        if not (
            isinstance(raw, list)
            and len(raw) <= MAX_CSS_CLASSES
            and all(_is_identifier(c) for c in raw)
        ):
            raise LayoutParseError(
                f"{where}.css_classes: must be a list of at most {MAX_CSS_CLASSES} "
                f"identifiers that match {IDENTIFIER.pattern}, each at most "
                f"{MAX_KEY_LENGTH} characters."
            )
        return raw

    def generated_key(self, prefix: str, last_number: int, where: str) -> None:
        """Bound the longest key a sequence generates before it allocates any."""
        # Compare the number first: str() fails above 4300 digits.
        if self.strict and (
            last_number >= 10**MAX_KEY_LENGTH
            or len(prefix) + len(str(last_number)) > MAX_KEY_LENGTH
        ):
            raise LayoutParseError(
                f"{where}.prefix: the generated keys are longer than "
                f"{MAX_KEY_LENGTH} characters."
            )

    def one_source(self, rows: Any, elements: Any, where: str) -> None:
        if self.strict and rows and elements:
            raise LayoutParseError(f"{where}: use either rows or elements, not both.")

    def take(self, count: int) -> None:
        """Reserve ``count`` elements before they are allocated; an empty entry costs one."""
        if not self.strict:
            return
        self.elements_left -= max(count, 1)
        if self.elements_left < 0:
            raise LayoutParseError(
                f"The layout expands to more than {MAX_EXPANDED_ELEMENTS} elements."
            )

    def render(self, canvas: CanvasConfig, where: str) -> None:
        """Reserve the grid cells that the CSS renderer writes for one view or variant."""
        if not self.strict:
            return
        self.cells_left -= canvas.columns * canvas.rows
        if self.cells_left < 0:
            raise LayoutParseError(
                f"{where}: the views and variants render more than "
                f"{MAX_RENDERED_CELLS} cells in total."
            )


# ── Public API ────────────────────────────────────────────────────────────────


def parse(yaml_text: str, *, strict: bool = False) -> NormalizedLayout:
    """
    Parse a YAML layout string and return a NormalizedLayout.

    ``strict=True`` also applies the bounded, strict import rules.
    Raises LayoutParseError on schema violations.
    """
    rules = _Rules(strict=strict)
    loader = _DepthLimitedLoader if strict else yaml.SafeLoader
    try:
        data = yaml.load(yaml_text, Loader=loader)
    except LayoutParseError:
        raise
    except (yaml.YAMLError, ValueError) as exc:
        # PyYAML raises ValueError for integers longer than Python's digit limit.
        raise LayoutParseError(f"Invalid YAML: {exc}") from exc
    except RecursionError as exc:
        raise LayoutParseError(
            "Invalid YAML: the document is nested too deeply."
        ) from exc

    if not isinstance(data, dict):
        raise LayoutParseError("Layout must be a YAML mapping at the top level.")
    if strict:
        _reject_recursive_aliases(data)
    data = rules.mapping(data, "layout", "layout")

    version = data.get("version", 1)
    if rules.integer(version, "version", 1) != SUPPORTED_VERSION:
        raise LayoutParseError(
            f"Unsupported layout version {version!r}. "
            f"This parser supports version {SUPPORTED_VERSION}."
        )

    meta = rules.optional_mapping(data.get("meta"), "meta", "meta")
    description = rules.text(meta.get("description", ""), "meta.description")
    global_bg = rules.background(meta.get("background", None), "meta")

    canvas_cfg = _parse_canvas(data.get("canvas", {}), rules, "canvas", global_bg)

    # Collect raw view definitions
    raw_views: dict[str, Any] = rules.named_mapping(data.get("views"), "views")
    raw_variants: dict[str, Any] = rules.named_mapping(data.get("variants"), "variants")

    layout = NormalizedLayout(description=description, source="yaml")
    layout.views = _compile_views(raw_views, canvas_cfg, global_bg, rules)
    _compile_variants(raw_variants, layout.views, canvas_cfg, rules)
    if strict:
        _check_elements(layout)
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


def _compile_views(
    raw_views: dict[str, Any],
    canvas_cfg: CanvasConfig,
    global_bg: str | None,
    rules: _Rules,
) -> dict[str, LayoutView]:
    # First pass: compile all views (copy_from resolution happens here)
    compiled_views: dict[str, LayoutView] = {}
    for view_name, view_data in raw_views.items():
        where = f"views.{view_name}"
        rules.text(view_name, f"{where} (view name)", _VIEW_NAMES)
        view_data = rules.mapping(view_data, "view", where)
        face = _parse_face(view_name)
        view_bg = rules.background(view_data.get("background"), where) or global_bg
        view_canvas = _parse_canvas(
            view_data.get("canvas", {}), rules, f"{where}.canvas", view_bg, canvas_cfg
        )
        rules.render(view_canvas, where)
        compiled_views[view_name] = _compile_view(
            view_name,
            view_data,
            face,
            view_canvas,
            raw_views,
            canvas_cfg,
            global_bg,
            rules,
        )

    return compiled_views


def _compile_variants(
    raw_variants: dict[str, Any],
    compiled_views: dict[str, LayoutView],
    canvas_cfg: CanvasConfig,
    rules: _Rules,
) -> None:
    # Second pass: compile variants into each affected view
    # The CSS renderer writes variants only for a layout with one face.
    if rules.strict and raw_variants and set(_VIEW_NAMES) <= compiled_views.keys():
        raise LayoutParseError(
            "variants: a layout with both a front and a rear view cannot have variants."
        )
    for variant_name, variant_data in raw_variants.items():
        where = f"variants.{variant_name}"
        if rules.strict and not (
            isinstance(variant_name, str) and VARIANT_NAME.fullmatch(variant_name)
        ):
            raise LayoutParseError(
                f"{where}: the variant name must match {VARIANT_NAME.pattern}."
            )
        variant_data = rules.mapping(variant_data, "variant", where)
        rules.text(variant_data.get("match", "module"), f"{where}.match", ("module",))
        variant_view_name = rules.text(
            variant_data.get("view", "front"), f"{where}.view", _VIEW_NAMES
        )
        if variant_view_name not in compiled_views:
            # An implicit view changes the faces that the CSS renderer writes.
            if rules.strict:
                raise LayoutParseError(
                    f"{where}.view: the layout has no {variant_view_name} view."
                )
            compiled_views[variant_view_name] = LayoutView(
                face=_parse_face(str(variant_view_name)),
                canvas=canvas_cfg,
            )
        view = compiled_views[variant_view_name]
        rules.render(view.canvas, where)
        variant_elements = _compile_rows(
            variant_data.get("rows", []),
            variant_data.get("elements", []),
            view.canvas,
            rules,
            where,
            variant_name=variant_name,
        )
        view.variants[variant_name] = variant_elements


def _parse_face(name: Any) -> Face:
    return _FACES.get(str(name).lower(), Face.FRONT)


def _parse_canvas(
    raw: Any,
    rules: _Rules,
    where: str,
    global_bg: str | None = None,
    parent: CanvasConfig | None = None,
) -> CanvasConfig:
    """Merge canvas config from raw dict, inheriting from parent if given."""
    raw = rules.mapping(raw, "canvas", where)
    p = parent or CanvasConfig()
    canvas = CanvasConfig(
        columns=rules.integer(raw.get("columns", p.columns), f"{where}.columns", 1),
        rows=rules.integer(
            raw.get("rows", p.rows), f"{where}.rows", 1, MAX_CANVAS_ROWS
        ),
        cell_size=rules.integer(
            raw.get("cell_size", p.cell_size), f"{where}.cell_size", 0, MAX_CELL_SIZE
        ),
        background=rules.background(
            raw.get("background", global_bg or p.background), where
        ),
    )
    if rules.strict and canvas.columns * canvas.rows > MAX_CANVAS_CELLS:
        raise LayoutParseError(
            f"{where}: the canvas has more than {MAX_CANVAS_CELLS} cells."
        )
    return canvas


def _compile_view(
    view_name: str,
    view_data: Any,
    face: Face,
    canvas: CanvasConfig,
    raw_views: dict[str, Any],
    global_canvas: CanvasConfig,
    global_bg: str | None,
    rules: _Rules,
) -> LayoutView:
    """Compile a single view, handling copy_from."""
    where = f"views.{view_name}"

    # Handle copy_from
    base_elements: list[PlacedElement] = []
    copy_from = view_data.get("copy_from")
    if copy_from is not None:
        rules.text(copy_from, f"{where}.copy_from")
    if copy_from:
        if copy_from not in raw_views:
            raise LayoutParseError(
                f"View '{view_name}' references copy_from='{copy_from}' "
                f"which does not exist."
            )
        src = rules.mapping(raw_views[copy_from], "view", f"views.{copy_from}")
        # The copy reads only the source's own rows, so a chain would drop content.
        if rules.strict and src.get("copy_from"):
            raise LayoutParseError(
                f"{where}: copy_from='{copy_from}' names a view that also uses "
                "copy_from. Cyclic or chained copy_from is not supported."
            )
        src_canvas = _parse_canvas(
            src.get("canvas", {}),
            rules,
            f"views.{copy_from}.canvas",
            global_bg,
            global_canvas,
        )
        base_elements = _compile_rows(
            src.get("rows", []),
            src.get("elements", []),
            src_canvas,
            rules,
            f"views.{copy_from}",
        )

    elements = _compile_rows(
        view_data.get("rows", []),
        view_data.get("elements", []),
        canvas,
        rules,
        where,
    )

    # Merge: base_elements first, then this view's elements override by key
    if base_elements:
        elements = replace_by_key(base_elements, elements)

    return LayoutView(face=face, canvas=canvas, elements=elements)


def _compile_rows(
    rows: list[Any],
    flat_elements: list[Any],
    canvas: CanvasConfig,
    rules: _Rules,
    where: str,
    variant_name: str | None = None,
) -> list[PlacedElement]:
    """
    Compile either a rows-based or flat elements-based definition into
    a list of PlacedElement objects.
    """
    rows = rules.items(rows, f"{where}.rows")
    flat_elements = rules.items(flat_elements, f"{where}.elements")
    rules.one_source(rows, flat_elements, where)
    if flat_elements:
        return _compile_flat_elements(
            flat_elements, canvas, rules, f"{where}.elements", variant_name
        )
    if rows:
        return _compile_rows_definition(
            rows, canvas, rules, f"{where}.rows", variant_name
        )
    return []


def _compile_flat_elements(
    elements: list[Any],
    canvas: CanvasConfig,
    rules: _Rules,
    where: str,
    variant_name: str | None,
) -> list[PlacedElement]:
    """Compile an explicit list of element dicts with at:/span: coordinates."""
    result: list[PlacedElement] = []
    for i, raw in enumerate(elements):
        if not isinstance(raw, dict):
            raise LayoutParseError(
                f"Element #{i} must be a mapping, got {type(raw).__name__}."
            )
        result.extend(
            _expand_element(raw, canvas, rules, f"{where}[{i}]", variant_name)
        )
    return result


def _compile_rows_definition(
    rows: list[Any],
    canvas: CanvasConfig,
    rules: _Rules,
    where: str,
    variant_name: str | None,
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
      - "top-odd"  → odd-numbered ports go in row 1
      - "top-even" → even-numbered ports go in row 1 (row 2 gets the odds)
    """
    result: list[PlacedElement] = []

    # We build a column cursor per row. row_cursors[1], row_cursors[2], …
    row_cursors: dict[int, int] = {r: 1 for r in range(1, canvas.rows + 1)}
    current_single_row = 1  # for "all"-pattern sequences

    for i, entry in enumerate(rows):
        if not isinstance(entry, dict):
            raise LayoutParseError(
                f"Row entry must be a mapping, got {type(entry).__name__}."
            )
        entry_where = f"{where}[{i}]"
        rules.one_of(entry, _ROW_ENTRY_KEYS, entry_where)

        if "elements" in entry:
            elements, row_cursors = _compile_explicit_row(
                entry["elements"],
                row_cursors,
                current_single_row,
                canvas,
                rules,
                f"{entry_where}.elements",
                variant_name,
            )
            result.extend(elements)
            current_single_row = min(current_single_row + 1, canvas.rows)

        elif "sequence" in entry:
            elements, row_cursors = _expand_sequence(
                entry["sequence"],
                row_cursors,
                canvas,
                rules,
                f"{entry_where}.sequence",
                variant_name,
            )
            result.extend(elements)

        elif "group" in entry:
            elements, row_cursors = _expand_group(
                entry["group"],
                row_cursors,
                canvas,
                rules,
                f"{entry_where}.group",
                variant_name,
            )
            result.extend(elements)

        elif "spacer" in entry:
            n = rules.integer(entry["spacer"], f"{entry_where}.spacer")
            spacer_elements, row_cursors = _insert_filler(
                ElementKind.SPACER,
                n,
                row_cursors,
                canvas,
                rules,
                variant_name,
                prefix="s",
            )
            result.extend(spacer_elements)

        elif "blank" in entry:
            n = rules.integer(entry["blank"], f"{entry_where}.blank")
            blank_elements, row_cursors = _insert_filler(
                ElementKind.BLANK,
                n,
                row_cursors,
                canvas,
                rules,
                variant_name,
                prefix="x",
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
    rules: _Rules,
    where: str,
    variant_name: str | None,
) -> tuple[list[PlacedElement], dict[int, int]]:
    """Place an explicit list of elements into ``current_row``."""
    result: list[PlacedElement] = []
    col = row_cursors.get(current_row, 1)
    elements = rules.items(elements, where)
    for i, raw in enumerate(elements):
        if isinstance(raw, str):
            rules.take(1)
            # shorthand: just a key name, infer kind
            kind = (
                ElementKind.SPACER
                if raw.startswith("s") and raw[1:].isdigit()
                else ElementKind.BLANK if raw in ("x", "y") else ElementKind.PORT
            )
            result.append(
                PlacedElement(
                    kind=kind, key=raw, row=current_row, col=col, variant=variant_name
                )
            )
            col += 1
        elif isinstance(raw, dict):
            expanded = _expand_element(
                raw,
                canvas,
                rules,
                f"{where}[{i}]",
                variant_name,
                default_row=current_row,
                default_col=col,
            )
            result.extend(expanded)
            col += sum(el.col_span for el in expanded)
        else:
            raise LayoutParseError(
                f"Element must be a string key or mapping, got {type(raw).__name__}."
            )
    row_cursors[current_row] = col
    return result, row_cursors


def _expand_sequence(
    seq: Any,
    row_cursors: dict[int, int],
    canvas: CanvasConfig,
    rules: _Rules,
    where: str,
    variant_name: str | None,
) -> tuple[list[PlacedElement], dict[int, int]]:
    """
    Expand a sequence definition into PlacedElement objects.

    sequence:
      kind: port           # default: port
      prefix: "gigabitethernet0-"
      start: 1             # first port number (default: 1)
      count: 24            # how many ports
      pattern: top-odd  # "top-odd" | "top-even" | "all"
      step: 1              # increment between ports (default: 1)
      row: 1               # for pattern: all only — target row (default: 1)
    """
    if not isinstance(seq, dict):
        raise LayoutParseError("'sequence' must be a mapping.")
    seq = rules.mapping(seq, "sequence", where)

    kind_str = rules.text(seq.get("kind", "port"), f"{where}.kind", tuple(_KIND_MAP))
    kind = _KIND_MAP.get(kind_str)
    if kind is None:
        raise LayoutParseError(f"{where}: Unknown element kind: {kind_str!r}.")

    prefix = rules.text(seq.get("prefix", ""), f"{where}.prefix")
    start = rules.integer(seq.get("start", 1), f"{where}.start")
    count = rules.integer(seq.get("count", 1), f"{where}.count")
    step = rules.integer(seq.get("step", 1), f"{where}.step", 1)
    pattern = seq.get("pattern", "top-odd")
    explicit_row = seq.get("row", None)
    if explicit_row is not None:
        explicit_row = rules.integer(explicit_row, f"{where}.row", 1, canvas.rows)

    rules.generated_key(prefix, start + max(count - 1, 0) * step, where)
    rules.take(count)
    # Generate port numbers
    port_numbers = [start + i * step for i in range(count)]

    result: list[PlacedElement] = []
    row_cursors = dict(row_cursors)  # copy

    if pattern == "all":
        # All ports in a single row (patch-panel style).
        # The target row defaults to 1 but can be overridden with `row:`.
        row = (
            explicit_row
            if explicit_row is not None
            else _single_active_row(row_cursors, canvas)
        )
        col = row_cursors.get(row, 1)
        for n in port_numbers:
            key = f"{prefix}{n}"
            result.append(
                PlacedElement(
                    kind=kind, key=key, row=row, col=col, variant=variant_name
                )
            )
            col += 1
        row_cursors[row] = col

    elif pattern in ("top-odd", "top-even"):
        # Two-row layout: odd ports in row 1, even ports in row 2
        # (or swapped for "top-even" pattern — useful when even ports are physically on top)
        row1, row2 = (1, 2) if canvas.rows >= 2 else (1, 1)
        if pattern == "top-even":
            row1, row2 = row2, row1

        # Find the starting column (max of the two row cursors to keep alignment)
        start_col = max(row_cursors.get(row1, 1), row_cursors.get(row2, 1))
        col1 = col2 = start_col

        for n in port_numbers:
            key = f"{prefix}{n}"
            if n % 2 == 1:  # odd
                result.append(
                    PlacedElement(
                        kind=kind, key=key, row=row1, col=col1, variant=variant_name
                    )
                )
                col1 += 1
            else:  # even
                result.append(
                    PlacedElement(
                        kind=kind, key=key, row=row2, col=col2, variant=variant_name
                    )
                )
                col2 += 1

        # Both cursors advance to the same column
        final_col = max(col1, col2)
        row_cursors[row1] = final_col
        row_cursors[row2] = final_col

    else:
        raise LayoutParseError(
            f"{where}: Unknown sequence pattern: {pattern!r}. "
            "Use 'top-odd', 'top-even', or 'all'."
        )

    return result, row_cursors


def _expand_group(
    group: Any,
    row_cursors: dict[int, int],
    canvas: CanvasConfig,
    rules: _Rules,
    where: str,
    variant_name: str | None,
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
    group = rules.mapping(group, "group", where)

    spacer_width = rules.integer(group.get("spacer", 1), f"{where}.spacer")
    sections = rules.items(group.get("sections", []), f"{where}.sections")
    result: list[PlacedElement] = []

    for i, section in enumerate(sections):
        section_where = f"{where}.sections[{i}]"
        if i > 0:
            # Insert spacer between sections
            spacer_els, row_cursors = _insert_filler(
                ElementKind.SPACER,
                spacer_width,
                row_cursors,
                canvas,
                rules,
                variant_name,
                prefix="s",
            )
            result.extend(spacer_els)

        if not isinstance(section, dict):
            raise LayoutParseError("Group section must be a mapping.")
        rules.one_of(section, _SECTION_KEYS, section_where)

        if "sequence" in section:
            els, row_cursors = _expand_sequence(
                section["sequence"],
                row_cursors,
                canvas,
                rules,
                f"{section_where}.sequence",
                variant_name,
            )
            result.extend(els)
        elif "elements" in section:
            row = _single_active_row(row_cursors, canvas)
            els, row_cursors = _compile_explicit_row(
                section["elements"],
                row_cursors,
                row,
                canvas,
                rules,
                f"{section_where}.elements",
                variant_name,
            )
            result.extend(els)
        elif "blank" in section:
            n = rules.integer(section["blank"], f"{section_where}.blank")
            els, row_cursors = _insert_filler(
                ElementKind.BLANK,
                n,
                row_cursors,
                canvas,
                rules,
                variant_name,
                prefix="x",
            )
            result.extend(els)
        else:
            raise LayoutParseError(
                f"Unknown group section keys: {list(section.keys())}."
            )

    return result, row_cursors


def _insert_filler(
    kind: ElementKind,
    count: int,
    row_cursors: dict[int, int],
    canvas: CanvasConfig,
    rules: _Rules,
    variant_name: str | None,
    prefix: str = "x",
) -> tuple[list[PlacedElement], dict[int, int]]:
    """Insert ``count`` filler elements spanning all active rows."""
    rules.take(count)
    result: list[PlacedElement] = []
    row_cursors = dict(row_cursors)
    start_col = max(row_cursors.get(r, 1) for r in range(1, canvas.rows + 1))

    for i in range(count):
        col = start_col + i
        # Generate a unique key that won't clash with port names
        key = f"{prefix}{col}"
        if canvas.rows >= 2:
            # One element spanning all rows
            result.append(
                PlacedElement(
                    kind=kind,
                    key=key,
                    row=1,
                    col=col,
                    row_span=canvas.rows,
                    col_span=1,
                    variant=variant_name,
                )
            )
        else:
            result.append(
                PlacedElement(kind=kind, key=key, row=1, col=col, variant=variant_name)
            )

    # Advance all row cursors
    new_col = start_col + count
    for r in range(1, canvas.rows + 1):
        row_cursors[r] = new_col

    return result, row_cursors


def _expand_element(
    raw: dict[str, Any],
    canvas: CanvasConfig,
    rules: _Rules,
    where: str,
    variant_name: str | None,
    default_row: int = 1,
    default_col: int = 1,
) -> list[PlacedElement]:
    """Compile a single explicit element dict."""
    raw = rules.mapping(raw, "element", where)
    rules.take(1)
    kind_str = rules.text(raw.get("kind", "port"), f"{where}.kind", tuple(_KIND_MAP))
    kind = _KIND_MAP.get(kind_str)
    if kind is None:
        raise LayoutParseError(f"{where}: Unknown element kind: {kind_str!r}.")

    at = rules.optional_mapping(raw.get("at"), "at", f"{where}.at")
    span = rules.optional_mapping(raw.get("span"), "span", f"{where}.span")

    # Elements may extend past their canvas, but not past the largest canvas.
    row = rules.integer(
        at.get("row", default_row), f"{where}.at.row", 1, MAX_CANVAS_ROWS
    )
    col = rules.integer(
        at.get("col", default_col), f"{where}.at.col", 1, MAX_CANVAS_CELLS
    )
    row_span = rules.integer(
        span.get("rows", 1), f"{where}.span.rows", 1, MAX_CANVAS_ROWS
    )
    col_span = rules.integer(
        span.get("cols", 1), f"{where}.span.cols", 1, MAX_CANVAS_CELLS
    )

    # key is required for port-like elements; optional for spacers/blanks
    key = raw.get("key", "")
    if rules.strict and "key" in raw and not _is_identifier(key):
        raise LayoutParseError(_key_error(where, key))
    if key is not None and not isinstance(key, str):
        raise LayoutParseError(f"{where}.key: must be a string.")
    if not key:
        if kind in _PORT_KINDS:
            raise LayoutParseError(f"Element of kind {kind_str!r} must have a 'key'.")
        # auto-generate for spacers/blanks
        key = f"{'s' if kind == ElementKind.SPACER else 'x'}{col}"

    label = raw.get("label")
    css_classes = (
        rules.css_classes(raw["css_classes"], where) if "css_classes" in raw else []
    )

    face_str = rules.text(raw.get("face", "both"), f"{where}.face", tuple(_FACES))
    face = _FACES.get(face_str, Face.BOTH)

    return [
        PlacedElement(
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
        )
    ]


def _single_active_row(row_cursors: dict[int, int], canvas: CanvasConfig) -> int:
    """Return the first row number (1) for single-row operations."""
    return 1


def _reject_recursive_aliases(data: Any) -> None:
    """Raise when a YAML alias refers to one of its own ancestors; visit shared nodes once."""
    on_path: set[int] = set()
    done: set[int] = set()
    stack: list[tuple[Any, bool]] = [(data, False)]
    while stack:
        node, leaving = stack.pop()
        if leaving:
            on_path.discard(id(node))
            done.add(id(node))
            continue
        if not isinstance(node, (dict, list)) or id(node) in done:
            continue
        if id(node) in on_path:
            raise LayoutParseError("Recursive YAML aliases are not supported.")
        on_path.add(id(node))
        stack.append((node, True))
        children = node.values() if isinstance(node, dict) else node
        stack.extend((child, False) for child in children)


def _check_elements(layout: NormalizedLayout) -> None:
    """Check the compiled elements of every view and variant against the strict rules."""
    if not any(view.elements for view in layout.views.values()):
        raise LayoutParseError("The layout must define at least one grid area.")
    for view_name, view in layout.views.items():
        element_sets = [(f"views.{view_name}", view.elements)]
        element_sets += [
            (f"variants.{name}", els) for name, els in view.variants.items()
        ]
        for where, elements in element_sets:
            for el in elements:
                _check_element(el, where)
    _check_painted_cells(layout)
    _check_area_shapes(layout)


def _check_painted_cells(layout: NormalizedLayout) -> None:
    """Bound the cells that the CSS renderer paints over all views and variants."""
    painted = 0
    for view_name, view in layout.views.items():
        # A variant renders the base elements, and its own elements replace them by key.
        base: Counter[str] = Counter()
        for el in view.elements:
            base[el.key] += _painted_cells(el, view.canvas)
        base_total = base.total()
        painted += base_total
        for elements in view.variants.values():
            keys = {el.key for el in elements}
            painted += base_total - sum(base[key] for key in keys)
            painted += sum(_painted_cells(el, view.canvas) for el in elements)
        if painted > MAX_PAINTED_CELLS:
            raise LayoutParseError(
                f"views.{view_name}: the views and variants paint more than "
                f"{MAX_PAINTED_CELLS} grid cells in total."
            )


def _check_area_shapes(layout: NormalizedLayout) -> None:
    """Check that each grid area that the CSS renderer writes is one rectangle."""
    for view_name, view in layout.views.items():
        renders = [(f"views.{view_name}", view.elements)]
        renders += [
            (f"variants.{name}", view.elements_for_variant(name))
            for name in view.variants
        ]
        for where, elements in renders:
            grid = area_grid(elements, view.canvas)
            _check_ports_keep_their_cells(elements, view.canvas, grid, where)
            area = non_rectangular_area(grid)
            if area is not None:
                raise LayoutParseError(
                    f"{where}: the cells of grid area {area!r} do not make one "
                    "rectangle. Elements must not overlap, and elements with one "
                    "key must fill one rectangle."
                )


def _check_ports_keep_their_cells(
    elements: list[PlacedElement],
    canvas: CanvasConfig,
    grid: list[list[str]],
    where: str,
) -> None:
    """Reject an element painted over a port; a port with no area is placed anywhere."""
    for el in elements:
        if el.is_port:
            for r, c in itertools.product(*painted_area(el, canvas)):
                if grid[r][c] != el.key:
                    raise LayoutParseError(
                        f"{where}: element {grid[r][c]!r} covers port {el.key!r}."
                    )


def _painted_cells(el: PlacedElement, canvas: CanvasConfig) -> int:
    """Return the cells the renderer writes for ``el``, at least one."""
    rows, cols = painted_area(el, canvas)
    return len(rows) * len(cols) or 1


def _key_error(where: str, key: Any) -> str:
    return (
        f"{where}: the element key {key!r:.80} must match {IDENTIFIER.pattern} "
        f"and have at most {MAX_KEY_LENGTH} characters."
    )


def _check_element(el: PlacedElement, where: str) -> None:
    if not _is_identifier(el.key):
        raise LayoutParseError(_key_error(where, el.key))
    if el.label is not None and not (
        isinstance(el.label, str)
        and el.label.isprintable()
        and len(el.label) <= MAX_LABEL_LENGTH
    ):
        raise LayoutParseError(
            f"{where}: element {el.key!r}: the label must be printable text of at most "
            f"{MAX_LABEL_LENGTH} characters."
        )
