"""
Legacy CSS adapter.

Wraps the existing ``grid_template_area`` CSS string in a NormalizedLayout so
that downstream code can treat both YAML and legacy sources uniformly.

The adapter does NOT attempt to parse or re-interpret the CSS — that would be
fragile and unnecessary.  Instead it stores the raw CSS string and marks the
layout as ``source="legacy_css"``.

Renderers that need raw CSS (i.e., the CSS Grid renderer in legacy mode) can
check ``layout.source`` and short-circuit to the raw string rather than
reconstructing it from elements.

This means ``NormalizedLayout.views`` will be empty for legacy layouts, and
``layout.raw_css`` holds the authoritative rendering string.  Future
renderers (SVG) would need to parse the CSS or prompt users to migrate to
YAML — but that is a problem for a future phase.
"""

from __future__ import annotations

from .model import NormalizedLayout


def wrap_legacy_css(css_text: str, description: str = "") -> NormalizedLayout:
    """
    Wrap a raw legacy CSS string in a NormalizedLayout.

    The resulting layout's ``source`` is ``"legacy_css"`` and its ``views``
    dict is empty.  The CSS text is stored on ``layout.raw_css``.

    Usage
    -----
    ::

        layout = wrap_legacy_css(device_view.grid_template_area)
        if layout.source == "legacy_css":
            css = layout.raw_css   # use as-is
        else:
            css = css_grid.render(layout)
    """
    layout = NormalizedLayout(description=description, source="legacy_css")
    layout.raw_css = css_text  # type: ignore[attr-defined]  # dynamic attribute
    return layout
