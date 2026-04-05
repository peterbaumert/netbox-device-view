"""
netbox_device_view.layout — YAML layout schema, parser, and renderers.

Public API
----------
  from netbox_device_view.layout import parse_yaml, render_css, validate_yaml

  # Parse a YAML layout string → NormalizedLayout
  layout = parse_yaml(yaml_text)

  # Render a NormalizedLayout → CSS string
  css = render_css(layout)

  # Validate YAML and return a list of error strings (empty = valid)
  errors = validate_yaml(yaml_text)

  # Get CSS from a DeviceView record (handles both YAML and legacy)
  css = get_css_for_device_view(device_view_obj)
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
from .renderers.css_grid import render

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
    # Renderer
    "render",
    # Convenience aliases
    "parse_yaml",
    "render_css",
    "validate_yaml",
    "get_css_for_device_view",
]

# Convenience aliases
parse_yaml = parse
render_css = render
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
        return render(layout)
    # Legacy path — return as-is
    return device_view.grid_template_area
