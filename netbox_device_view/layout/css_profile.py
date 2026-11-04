"""
The CSS import profile: the small CSS subset that a Data Source import accepts.

It covers the bundled layouts and the CSS that the renderer generates from
YAML. It is not a general CSS sanitizer.
"""

from __future__ import annotations

import re
from typing import Any

import tinycss2

from .parser import IDENTIFIER, VARIANT_NAME, LayoutParseError, one_css_color
from .renderers.css_grid import (
    AUTO_SIZING_PROPERTIES,
    FRONT_CLASS,
    GRID_CLASSES,
    MODULE_CLASS_PREFIX,
    REAR_CLASS,
    non_rectangular_area,
)

_REQUIRED_CLASSES = set(GRID_CLASSES)
_FACE_CLASSES = {FRONT_CLASS, REAR_CLASS}
_MODULE_CLASS = re.compile(f"{MODULE_CLASS_PREFIX}{VARIANT_NAME.pattern}")
_BACKGROUNDS = ("background", "background-color")
_CSS_WHITESPACE = re.compile(r"[ \t\n\r\f]+")
_EMPTY_CELL = re.compile(r"\.+")
# A rule block holds a color function at most; deeper input is not a layout.
_MAX_NESTING = 8


def validate_css(css: str) -> None:
    """Raise LayoutParseError unless ``css`` is inside the import profile."""
    tokens = tinycss2.parse_component_value_list(css)
    nodes = _nodes(tokens)
    # tinycss2 closes open blocks and decodes escapes, so accepted text must serialize unchanged.
    if tinycss2.serialize(tokens) != css or any(node.type == "error" for node in nodes):
        raise LayoutParseError(
            "CSS parse error: close every block, string and comment, and use no escapes."
        )
    for node in nodes:
        value = getattr(node, "value", None)
        if isinstance(value, str) and ("<" in value or ">" in value):
            raise LayoutParseError("CSS must not contain the HTML delimiters < or >.")
        if node.type == "url" or (node.type == "function" and node.lower_name == "url"):
            raise LayoutParseError("CSS must not contain a URL.")
    names_area = False
    for rule in tinycss2.parse_stylesheet(
        css, skip_comments=True, skip_whitespace=True
    ):
        names_area = _check_rule(rule) or names_area
    if not names_area:
        raise LayoutParseError("The CSS must define at least one grid area.")


def _check_rule(rule: Any) -> bool:
    """Check one rule; return True when it names a grid area."""
    _reject_error_or_at_rule(rule)
    _check_selector(rule.prelude)
    names_area = False
    for item in tinycss2.parse_blocks_contents(
        rule.content, skip_comments=True, skip_whitespace=True
    ):
        _reject_error_or_at_rule(item)
        if item.type != "declaration":
            raise LayoutParseError("Nested rules are not accepted.")
        if item.name.startswith("--"):
            raise LayoutParseError(f"{item.name}: custom properties are not accepted.")
        if item.important:
            raise LayoutParseError(f"{item.name}: !important is not accepted.")
        names_area = _check_declaration(item) or names_area
    return names_area


def _reject_error_or_at_rule(node: Any) -> None:
    if node.type == "error":
        raise LayoutParseError(f"CSS parse error: {node.message}")
    if node.type == "at-rule":
        raise LayoutParseError(f"@{node.at_keyword}: at-rules are not accepted.")


def _check_declaration(declaration: Any) -> bool:
    """Check one declaration; return True when it names a grid area."""
    name = declaration.lower_name
    values = [
        token
        for token in declaration.value
        if token.type not in ("whitespace", "comment")
    ]
    if name == "grid-template-areas":
        return _check_grid_areas(values)
    if name in AUTO_SIZING_PROPERTIES:
        if (
            len(values) != 1
            or values[0].type != "ident"
            or values[0].lower_value != "auto"
        ):
            raise LayoutParseError(f"{declaration.name}: must be auto.")
    elif name in _BACKGROUNDS:
        if one_css_color(values) is None:
            raise LayoutParseError(
                f"{declaration.name}: must be one literal CSS color."
            )
    else:
        raise LayoutParseError(f"{declaration.name}: the property is not accepted.")
    return False


def _check_grid_areas(values: list[Any]) -> bool:
    """Accept strings of area identifiers or dots with one row width and rectangular areas."""
    rows = []
    if not values:
        raise LayoutParseError("grid-template-areas: the value is empty.")
    for token in values:
        cells = (
            [cell for cell in _CSS_WHITESPACE.split(token.value) if cell]
            if token.type == "string"
            else []
        )
        if not cells or not all(
            IDENTIFIER.fullmatch(cell) or _EMPTY_CELL.fullmatch(cell) for cell in cells
        ):
            raise LayoutParseError(
                "grid-template-areas: each row must be a string of area "
                "identifiers or dots."
            )
        rows.append(cells)
    if len({len(row) for row in rows}) != 1:
        raise LayoutParseError(
            "grid-template-areas: every row must have the same number of cells."
        )
    area = non_rectangular_area(rows)
    if area is not None:
        raise LayoutParseError(
            f"grid-template-areas: the cells of area {area!r} must make one rectangle."
        )
    return any(IDENTIFIER.fullmatch(cell) for row in rows for cell in row)


def _check_selector(prelude: list[Any]) -> None:
    """Accept one compound class selector: deviceview, area, one module class, one face."""
    tokens = list(prelude)
    while tokens and tokens[0].type == "whitespace":
        tokens.pop(0)
    while tokens and tokens[-1].type == "whitespace":
        tokens.pop()
    pairs = [tokens[i : i + 2] for i in range(0, len(tokens), 2)]
    classes = [
        pair[1].value
        for pair in pairs
        if len(pair) == 2
        and pair[0].type == "literal"
        and pair[0].value == "."
        and pair[1].type == "ident"
    ]
    extra = set(classes) - _REQUIRED_CLASSES
    faces = extra & _FACE_CLASSES
    modules = {name for name in extra if _MODULE_CLASS.fullmatch(name)}
    if (
        not pairs
        or len(classes) != len(pairs)
        or len(set(classes)) != len(classes)
        or not _REQUIRED_CLASSES <= set(classes)
        or extra != faces | modules
        or len(faces) > 1
        or len(modules) > 1
        or not all(IDENTIFIER.fullmatch(name) for name in classes)
    ):
        raise LayoutParseError(
            f"{tinycss2.serialize(prelude).strip()!r:.80}: the selector must be one "
            "compound class selector with deviceview and area, and optionally one "
            "module class and dFront or dRear."
        )


def _nodes(tokens: list[Any]) -> list[Any]:
    """Return every node, including the nodes inside blocks and functions."""
    # tinycss2.serialize() recurses, so the nesting depth is bounded first.
    nodes: list[Any] = []
    pending = [(tokens, 1)]
    while pending:
        children, depth = pending.pop()
        if depth > _MAX_NESTING:
            raise LayoutParseError("CSS parse error: the CSS is nested too deeply.")
        for node in children:
            nodes.append(node)
            for inner in (
                getattr(node, "content", None),
                getattr(node, "arguments", None),
            ):
                if inner:
                    pending.append((inner, depth + 1))
    return nodes
