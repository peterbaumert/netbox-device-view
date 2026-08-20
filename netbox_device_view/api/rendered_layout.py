"""
Rendered-layout composition for the device rendered-layout API endpoint.

Bridges the layout engine (``netbox_device_view.layout`` — pure, NetBox-
agnostic geometry) with real NetBox component objects (Interface,
ConsolePort, FrontPort, RearPort) to produce the versioned JSON envelope
consumed by API clients (e.g. the netbox-companion mobile app).

This computes connection-state, cable-color, and peer-summary information
server-side, replicating exactly what ``ports.html``'s inline JavaScript
(``applyPortStatus()``) currently does client-side for the web tab — see
that template for the reference behaviour this mirrors. Device/DeviceView
selection reuses ``utils.py``'s ``_detect_variant``/``_rack_sort_key`` and
the same stylename derivation as ``process_interfaces``/``process_ports``,
so layout selection stays identical to the existing web view.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from dcim.models import ConsolePort, Device, FrontPort, Interface, RearPort
from django.contrib.contenttypes.prefetch import GenericPrefetch

from ..layout import parse as parse_layout
from ..layout.model import Face, LayoutView
from ..layout.parser import LayoutParseError
from ..layout.renderers.svg import render_api_view, svg_dims
from ..models import DeviceView
from ..utils import (
    _derive_interface_stylename,
    _derive_port_stylename,
    _detect_variant,
    _rack_sort_key,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Status colours -- mirror device_view_svg.css's Bootstrap-derived palette.
# Baked in as explicit SVG fill attributes since flutter_svg (the
# netbox-companion mobile client's renderer) cannot resolve the CSS classes
# the web renderer relies on for the same states.
COLOUR_CONNECTED = "#198754"  # bg-success
COLOUR_ENABLED = "#6c757d"  # bg-secondary (enabled, not connected)
COLOUR_DISABLED = "#dc3545"  # bg-danger
COLOUR_PARTIAL = "#ffc107"  # bg-warning
NO_COLOR_FILL = "url(#dv-nocolor-pattern)"

_HEX_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{6}$")

# Component types with a NetBox REST /trace/ action (PathEndpointMixin in
# dcim/api/views.py). FrontPort/RearPort use PassThroughPortMixin instead
# and have no /trace/ action.
_TRACE_SUPPORTED_MODELS = {"interface", "consoleport"}


class UnavailableReason:
    NO_LAYOUT = "no_layout"
    SVG_LAYOUT_REQUIRED = "svg_layout_required"
    INVALID_LAYOUT = "invalid_layout"


@dataclass
class ComponentStatus:
    component: object
    object_type: str
    enabled: bool
    connection_state: (
        str  # "connected" | "partially_connected" | "enabled" | "disabled"
    )
    cable_color: str | None  # None = no cable, "" = cable with no color set
    peer_labels: list[str] = field(default_factory=list)


def _peer_label(peer) -> str:
    device = getattr(peer, "device", None)
    name = getattr(peer, "name", None)
    if device and name:
        return f"{device} | {name}"
    return str(peer)


def compute_component_status(component, *, is_port: bool) -> ComponentStatus:
    """Replicate ports.html's applyPortStatus() JS, in Python, for one component."""
    object_type = f"dcim.{type(component)._meta.model_name}"

    if is_port:
        enabled = True
        link_peers = list(component.link_peers)
        connected = bool(link_peers)
        partially_connected = False
        peers = link_peers
    else:
        enabled = bool(component.enabled)
        connected_endpoints = list(component.connected_endpoints or [])
        link_peers = list(component.link_peers)
        connected = bool(connected_endpoints)
        partially_connected = bool(link_peers) and not connected
        peers = connected_endpoints or link_peers

    if connected:
        state = "connected"
    elif partially_connected:
        state = "partially_connected"
    elif enabled:
        state = "enabled"
    else:
        state = "disabled"

    cable = getattr(component, "cable", None)
    cable_color = cable.color if cable else None

    return ComponentStatus(
        component=component,
        object_type=object_type,
        enabled=enabled,
        connection_state=state,
        cable_color=cable_color,
        peer_labels=[_peer_label(p) for p in peers],
    )


def _fill_for(status: ComponentStatus) -> str:
    if status.cable_color is not None:
        if status.cable_color == "":
            return NO_COLOR_FILL
        if _HEX_COLOR_RE.match(status.cable_color):
            return f"#{status.cable_color}"
        # Malformed value somehow stored on the model — don't trust it raw
        # in hand-built SVG; fall through to the state colour instead.
        logger.warning(
            "Ignoring non-hex cable color %r on %s",
            status.cable_color,
            status.component,
        )

    return {
        "connected": COLOUR_CONNECTED,
        "partially_connected": COLOUR_PARTIAL,
        "enabled": COLOUR_ENABLED,
        "disabled": COLOUR_DISABLED,
    }[status.connection_state]


def _component_querysets(device):
    """Return (interfaces, console_ports, front_ports, rear_ports) querysets
    for one device, with N+1-avoiding prefetches matching the same
    prefetch shape NetBox's own InterfaceViewSet/ConsolePortViewSet/
    FrontPortViewSet/RearPortViewSet use (dcim/api/views.py)."""
    interfaces = (
        Interface.objects.filter(device=device)
        .exclude(type__in=("virtual", "lag"))
        .prefetch_related(
            GenericPrefetch(
                "cable__terminations__termination",
                [Interface.objects.select_related("device", "cable")],
            ),
            GenericPrefetch(
                "_path__path_objects",
                [Interface.objects.select_related("device", "cable")],
            ),
        )
    )
    console_ports = ConsolePort.objects.filter(device=device).prefetch_related(
        "_path", "cable__terminations"
    )
    front_ports = (
        FrontPort.objects.filter(device=device)
        .exclude(type="virtual")
        .prefetch_related("cable__terminations")
    )
    rear_ports = (
        RearPort.objects.filter(device=device)
        .exclude(type="virtual")
        .prefetch_related("cable__terminations")
    )
    return interfaces, console_ports, front_ports, rear_ports


def _stylename_map(device) -> dict[str, ComponentStatus]:
    """Return {stylename: ComponentStatus} for every eligible component on
    one device — the same key space PlacedElement.key ("stylename") uses."""
    interfaces, console_ports, front_ports, rear_ports = _component_querysets(device)

    by_stylename: dict[str, ComponentStatus] = {}
    for itf in interfaces:
        by_stylename[_derive_interface_stylename(itf.name)] = compute_component_status(
            itf, is_port=False
        )
    for port in list(console_ports) + list(front_ports) + list(rear_ports):
        by_stylename[_derive_port_stylename(port.name)] = compute_component_status(
            port, is_port=True
        )
    return by_stylename


def _unavailable(device_id: int, reason: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "available": False,
        "reason": reason,
        "device_id": device_id,
        "render_mode": "svg",
        "panels": [],
    }


def compose_rendered_layout(device: Device) -> dict:
    """
    Compose the full rendered-layout JSON envelope for one device.

    Handles both plain devices and virtual chassis (one set of panels per
    member, in rack order) — mirrors utils.prepare_svg()'s branching.
    Returns a plain dict matching the API's documented schema; never
    raises for expected "no layout"/"invalid layout" conditions (those are
    reported via ``available``/``reason`` instead).
    """
    if device.virtual_chassis is None:
        members = [device]
    else:
        members = sorted(
            device.virtual_chassis.members.select_related("rack").all(),
            key=_rack_sort_key,
        )

    # First pass: resolve every member's DeviceView. Any missing/CSS-only
    # DeviceView fails the whole response (matches prepare_svg()'s
    # all-or-nothing ObjectDoesNotExist behaviour for virtual chassis).
    member_views: dict[int, DeviceView] = {}
    for member in members:
        try:
            member_views[member.pk] = DeviceView.objects.get(
                device_type=member.device_type
            )
        except DeviceView.DoesNotExist:
            return _unavailable(device.pk, UnavailableReason.NO_LAYOUT)

    for member in members:
        if not member_views[member.pk].has_yaml_layout:
            return _unavailable(device.pk, UnavailableReason.SVG_LAYOUT_REQUIRED)

    panels = []
    try:
        for member in members:
            device_view = member_views[member.pk]
            normalized = parse_layout(device_view.yaml_layout)
            member_modules = list(member.modules.all())
            variant = _detect_variant(member_modules, device_view)
            status_by_stylename = _stylename_map(member)

            member_prefix = f"member-{member.pk}-" if len(members) > 1 else ""
            member_suffix = f" ({member.name})" if len(members) > 1 else ""

            face_views: list[tuple[str, str, LayoutView]] = []
            if normalized.has_separate_faces():
                if normalized.front:
                    face_views.append(("front", "Front", normalized.front))
                if normalized.rear:
                    face_views.append(("rear", "Rear", normalized.rear))
            else:
                view = normalized.front or normalized.rear
                if view is not None:
                    face_label = "Rear" if view.face == Face.REAR else "Front"
                    face_key = "rear" if view.face == Face.REAR else "front"
                    face_views.append((face_key, face_label, view))

            for face_key, face_label, view in face_views:
                svg, geometry = render_api_view(
                    view,
                    fills={k: _fill_for(v) for k, v in status_by_stylename.items()},
                    variant_name=variant,
                )
                hotspots = []
                for cell in geometry:
                    status = status_by_stylename.get(cell["key"])
                    if status is None:
                        continue
                    component = status.component
                    hotspots.append(
                        {
                            "object_type": status.object_type,
                            "object_id": component.pk,
                            "name": component.name,
                            "x": cell["x"],
                            "y": cell["y"],
                            "width": cell["width"],
                            "height": cell["height"],
                            "enabled": status.enabled,
                            "connection_state": status.connection_state,
                            "cable_color": status.cable_color,
                            "peer_labels": status.peer_labels,
                            "trace_supported": type(component)._meta.model_name
                            in _TRACE_SUPPORTED_MODELS,
                        }
                    )
                width, height = svg_dims(view.canvas)
                panels.append(
                    {
                        "key": f"{member_prefix}{face_key}",
                        "label": f"{face_label}{member_suffix}",
                        "width": width,
                        "height": height,
                        "svg": svg,
                        "hotspots": hotspots,
                    }
                )
    except LayoutParseError:
        logger.warning(
            "Invalid YAML layout for device %s (device_type %s)",
            device.pk,
            device.device_type_id,
        )
        return _unavailable(device.pk, UnavailableReason.INVALID_LAYOUT)
    except Exception:
        logger.exception(
            "Unexpected error composing rendered layout for device %s", device.pk
        )
        return _unavailable(device.pk, UnavailableReason.INVALID_LAYOUT)

    return {
        "schema_version": SCHEMA_VERSION,
        "available": True,
        "reason": None,
        "device_id": device.pk,
        "render_mode": "svg",
        "panels": panels,
    }
