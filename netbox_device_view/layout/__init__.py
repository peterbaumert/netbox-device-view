"""
netbox_device_view.layout — YAML layout schema, parser, and renderers.

Public API
----------
  from netbox_device_view.layout import parse_yaml, render_css, render_svg, validate_yaml

  # Parse a YAML layout string → NormalizedLayout
  layout = parse_yaml(yaml_text)

  # Render a NormalizedLayout → CSS string (CSS Grid renderer)
  css = render_css(layout)

  # Render a NormalizedLayout → SVG string (SVG renderer)
  svg = render_svg(layout)

  # Validate YAML and return a list of error strings (empty = valid)
  errors = validate_yaml(yaml_text)

  # Get CSS from a DeviceView record (handles both YAML and legacy)
  css = get_css_for_device_view(device_view_obj)

  # Get SVG from a DeviceView record (requires YAML layout)
  svg = get_svg_for_device_view(device_view_obj)
"""

from .legacy import wrap_legacy_css
from .model import (
    CanvasConfig,
    ElementKind,
    Face,
    LayoutView,
    NormalizedLayout,
    PlacedElement,
)
from .parser import LayoutParseError, parse, validate
from .renderers.css_grid import render as _render_css
from .renderers.svg import render as _render_svg

__all__ = [
    # Model
    "NormalizedLayout",
    "LayoutView",
    "PlacedElement",
    "CanvasConfig",
    "ElementKind",
    "Face",
    # Parser
    "parse",
    "validate",
    "LayoutParseError",
    # Legacy adapter
    "wrap_legacy_css",
    # Renderers
    "render_css",
    "render_svg",
    # Convenience aliases
    "parse_yaml",
    "validate_yaml",
    "get_css_for_device_view",
    "get_svg_for_device_view",
]

# Convenience aliases
parse_yaml = parse
render_css = _render_css
render_svg = _render_svg
validate_yaml = validate


def get_css_for_device_view(device_view) -> str:
    """
    Return the CSS string to inject for a DeviceView record.

    Prefers YAML layout when present; falls back to legacy CSS.

    Parameters
    ----------
    device_view : DeviceView
        A DeviceView model instance with ``yaml_layout`` and
        ``grid_template_area`` fields.

    Returns
    -------
    str
        Raw CSS text ready to inject into a <style> block.
    """
    if device_view.has_yaml_layout:
        layout = parse(device_view.yaml_layout)
        return _render_css(layout)
    # Legacy path — return as-is
    return device_view.grid_template_area


def get_svg_for_device_view(device_view, variant_name=None) -> str:
    """
    Return an SVG string for a DeviceView record.

    Only works when the device view has a YAML layout; returns an empty
    string for legacy CSS-only records (the caller must fall back to CSS
    rendering in that case).

    Parameters
    ----------
    device_view : DeviceView
        A DeviceView model instance with ``yaml_layout`` field.
    variant_name : str | None
        Optional module variant name to render.

    Returns
    -------
    str
        SVG markup ready for ``{% autoescape off %}`` injection, or ``""``
        if the record has no YAML layout.
    """
    if not device_view.has_yaml_layout:
        return ""
    layout = parse(device_view.yaml_layout)
    return _render_svg(layout, variant_name=variant_name)
