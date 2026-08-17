"""
Normalized internal layout model.

This module defines the canonical in-memory representation that both the
YAML parser and the legacy CSS adapter produce. All renderers (CSS Grid,
and future SVG) consume this model exclusively, so the schema can stay
stable while renderers are added or changed independently.

Data classes are intentionally plain Python (no Django dependency) so they
can be used in tests and tooling without a running NetBox instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ElementKind(str, Enum):
    """The logical type of a layout element."""

    PORT = "port"
    INTERFACE = "interface"
    CONSOLE_PORT = "console-port"
    POWER_PORT = "power-port"
    MODULE_SLOT = "module-slot"
    SPACER = "spacer"
    LABEL = "label"
    BLANK = "blank"  # decorative filler (x / y in legacy CSS)


class Face(str, Enum):
    """Which physical face of the device this element belongs to."""

    FRONT = "front"
    REAR = "rear"
    BOTH = "both"  # applies to both (default for 1U switches)


@dataclass
class PlacedElement:
    """
    A single element placed on the device layout grid.

    Coordinates use 1-based row/col to match CSS grid conventions.
    ``span`` defaults to (1, 1) — a single cell.

    ``key`` is the CSS grid-area name / stylename that links this element
    to a real NetBox port or interface object.  For spacers and blanks the
    key is just a unique placeholder name (e.g. ``s0``, ``x0``).
    """

    kind: ElementKind
    key: str  # CSS grid-area name / stylename
    face: Face = Face.BOTH

    # Grid placement (1-based, matching CSS grid-row / grid-column)
    row: int = 1
    col: int = 1
    row_span: int = 1
    col_span: int = 1

    # Optional display label (for label elements and future SVG tooltips)
    label: str | None = None

    # Extra CSS class names to attach (for styling hooks)
    css_classes: list[str] = field(default_factory=list)

    # Variant context: if set, this element only appears when the named
    # module is installed (mirrors .deviceview.module<Name>.area in CSS)
    variant: str | None = None

    @property
    def is_port(self) -> bool:
        """True for elements that correspond to a real NetBox port/interface."""
        return self.kind not in (
            ElementKind.SPACER,
            ElementKind.BLANK,
            ElementKind.LABEL,
        )


@dataclass
class CanvasConfig:
    """
    Physical canvas dimensions.

    ``columns`` and ``rows`` describe the grid.  ``cell_size`` (px) is used
    by the SVG renderer; the CSS Grid renderer ignores it (CSS uses 20 px
    cells defined in device_view.css).
    """

    columns: int = 32
    rows: int = 2
    cell_size: int = 20  # pixels — for SVG renderer
    background: str | None = None  # CSS color string


@dataclass
class LayoutView:
    """
    A single rendered view (front or rear).

    Contains an ordered list of PlacedElement objects and a canvas config.
    Elements are ordered by (row, col) but renderers may reorder them.
    """

    face: Face
    canvas: CanvasConfig
    elements: list[PlacedElement] = field(default_factory=list)

    # variant_name → list[PlacedElement] overrides/additions
    variants: dict[str, list[PlacedElement]] = field(default_factory=dict)

    def elements_for_variant(
        self, variant_name: str | None = None
    ) -> list[PlacedElement]:
        """
        Return the full element list for a given module variant.

        Base elements without a variant always apply.  If ``variant_name``
        is given, elements tagged with that variant are also included,
        replacing any base element with the same key.
        """
        base = {el.key: el for el in self.elements if el.variant is None}
        if variant_name and variant_name in self.variants:
            for el in self.variants[variant_name]:
                base[el.key] = el
        return list(base.values())


@dataclass
class NormalizedLayout:
    """
    The top-level normalized layout for a device type.

    A device type may have a ``front`` view, a ``rear`` view, or both.
    Patch panels typically expose both; switches usually only ``front``.

    This is the object that renderers receive.
    """

    # Human-readable description (from YAML meta or CSS comment)
    description: str = ""

    # Source format tag — informational only
    source: str = "yaml"  # "yaml" | "legacy_css"

    views: dict[str, LayoutView] = field(default_factory=dict)

    @property
    def front(self) -> LayoutView | None:
        return self.views.get(Face.FRONT.value) or self.views.get("front")

    @property
    def rear(self) -> LayoutView | None:
        return self.views.get(Face.REAR.value) or self.views.get("rear")

    def has_separate_faces(self) -> bool:
        """True when the device exposes distinct front and rear views."""
        return "front" in self.views and "rear" in self.views
