import re
import unicodedata
from collections import defaultdict
from itertools import chain

from dcim.models import (
    Cable,
    ConsolePortTemplate,
    ConsoleServerPortTemplate,
    FrontPortTemplate,
    InterfaceTemplate,
    RearPortTemplate,
    VirtualChassis,
)
from django.core.exceptions import ObjectDoesNotExist

from .layout import (
    LayoutParseError,
    get_css_for_device_view,
    get_svg_for_device_view,
)
from .layout import (
    parse as parse_layout,
)
from .models import DeviceView


def normalize_grid_template_area(value: str) -> str:
    """
    Replace every Unicode space separator (category Zs) with an ASCII space; keep newlines.

    Pasted text often holds U+00A0 and similar spaces, which grid-template-areas rejects.
    """
    return "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in value)


def _derive_interface_stylename(name):
    regex = r"^(?P<type>([a-zA-Z\-_]*))(\/|(?P<dev>[0-9]+).|\s)?((?P<module>[0-9]+).|\s)?((?P<port>[0-9]+))$"
    matches = re.search(regex, name.lower())
    if matches:
        if not matches["type"]:
            stylename = matches["port"]
        else:
            stylename = (
                matches["type"] + (matches["module"] or "") + "-" + matches["port"]
            )
    else:
        stylename = re.sub(r"[^.a-zA-Z\d]", "-", name.lower())
    if stylename.isdigit():
        stylename = f"p{stylename}"
    return stylename


def _derive_port_stylename(name):
    stylename = re.sub(r"[^.a-zA-Z\d]", "-", name.lower())
    if stylename.isdigit():
        stylename = f"p{stylename}"
    return stylename


def get_stylenames_for_device_type(device_type_id):
    """Return a list of dicts with name, stylename, and kind for all component
    templates of the given DeviceType.  Mirrors the stylename logic used at
    render-time so the Add/Edit form can preview CSS grid-area names before a
    DeviceView is saved.
    """
    results = []

    for tmpl in InterfaceTemplate.objects.filter(
        device_type_id=device_type_id
    ).order_by("name"):
        if tmpl.type in ("virtual", "lag"):
            continue
        results.append(
            {
                "name": tmpl.name,
                "stylename": _derive_interface_stylename(tmpl.name),
                "kind": "Interface",
                "type": tmpl.get_type_display(),
            }
        )

    for tmpl in ConsolePortTemplate.objects.filter(
        device_type_id=device_type_id
    ).order_by("name"):
        results.append(
            {
                "name": tmpl.name,
                "stylename": _derive_port_stylename(tmpl.name),
                "kind": "Console Port",
                "type": tmpl.get_type_display(),
            }
        )

    for tmpl in ConsoleServerPortTemplate.objects.filter(
        device_type_id=device_type_id
    ).order_by("name"):
        results.append(
            {
                "name": tmpl.name,
                "stylename": _derive_port_stylename(tmpl.name),
                "kind": "Console Server Port",
                "type": tmpl.get_type_display(),
            }
        )

    for tmpl in FrontPortTemplate.objects.filter(
        device_type_id=device_type_id
    ).order_by("name"):
        results.append(
            {
                "name": tmpl.name,
                "stylename": _derive_port_stylename(tmpl.name),
                "kind": "Front Port",
                "type": tmpl.get_type_display(),
            }
        )

    for tmpl in RearPortTemplate.objects.filter(device_type_id=device_type_id).order_by(
        "name"
    ):
        results.append(
            {
                "name": tmpl.name,
                "stylename": _derive_port_stylename(tmpl.name),
                "kind": "Rear Port",
                "type": tmpl.get_type_display(),
            }
        )

    return results


def device_height_px(device_type):
    """Return the CSS pixel height for a device grid based on u_height.

    Each U is rendered as two rows of 20 px with 2 px of spacing per U.
    """
    return device_type.u_height * 2 * 20 + device_type.u_height * 2


def active_panel_key(obj):
    """Return the ports_chassis/svg_views key that corresponds to ``obj``,
    or ``None`` for a standalone (non-virtual-chassis) device.

    Mirrors the key scheme used internally by ``prepare()``/``prepare_svg()``:
    a virtual chassis member is keyed by its ``vc_position``. Used to
    highlight the specific device the user navigated to when viewing a
    virtual chassis, whose device page shows every member's panel stacked
    together. Returns ``None`` for a standalone device since there is only
    ever one panel in that case, making a highlight redundant.
    """
    if obj.virtual_chassis is None:
        return None
    return obj.vc_position


def _detect_variant(member_modules, device_view) -> str | None:
    """Return the first installed module model name that matches a known variant,
    or None if no variant applies."""
    if not device_view.has_yaml_layout:
        return None
    layout = parse_layout(device_view.yaml_layout)
    view = layout.front or layout.rear
    if view is None or not view.variants:
        return None
    installed_models = {m.module_type.model for m in member_modules}
    for variant_name in view.variants:
        if variant_name in installed_models:
            return variant_name
    return None


def process_interfaces(interfaces, ports_chassis, dev):
    if interfaces is not None:
        for itf in interfaces:
            if itf.type == "virtual" or itf.type == "lag":
                continue
            regex = r"^(?P<type>([a-zA-Z\-_]*))(\/|(?P<dev>[0-9]+).|\s)?((?P<module>[0-9]+).|\s)?((?P<port>[0-9]+))$"
            matches = re.search(regex, itf.name.lower())
            if matches:
                if not matches["type"]:
                    # No alphabetic prefix — port number only; isdigit() below adds 'p'
                    itf.stylename = matches["port"]
                else:
                    itf.stylename = (
                        matches["type"]
                        + (matches["module"] or "")
                        + "-"
                        + matches["port"]
                    )
            else:
                itf.stylename = re.sub(r"[^.a-zA-Z\d]", "-", itf.name.lower())
            if itf.stylename.isdigit():
                itf.stylename = f"p{itf.stylename}"

            if dev not in ports_chassis:
                ports_chassis[dev] = []
            ports_chassis[dev].append(itf)
    return ports_chassis


def process_console_ports(device, ports_chassis, dev, user):
    """Add the console ports and console-server ports of ``device`` that ``user`` can view."""
    for ports in (device.consoleports, device.consoleserverports):
        ports_chassis = process_ports(ports.restrict(user, "view"), ports_chassis, dev)
    return ports_chassis


def process_ports(ports, ports_chassis, dev):
    if ports is not None:
        for port in ports:
            if port.type == "virtual":
                continue
            port.is_port = True
            port.stylename = re.sub(r"[^.a-zA-Z\d]", "-", port.name.lower())
            if port.stylename.isdigit():
                port.stylename = f"p{port.stylename}"
            if dev not in ports_chassis:
                ports_chassis[dev] = []
            ports_chassis[dev].append(port)
    return ports_chassis


def _rack_sort_key(member):
    """Sort key for virtual chassis members by physical rack position.

    Respects the rack's ``desc_units`` flag:
      - desc_units=False (default): U1 is at the bottom → higher U = higher in rack
        → sort descending so the top-of-rack device appears first.
      - desc_units=True: U1 is at the top → lower U = higher in rack
        → sort ascending so the top-of-rack device appears first.

    Members without a rack position are placed last, tiebroken by vc_position.
    """
    pos = member.position
    vc = member.vc_position if member.vc_position is not None else 0
    if pos is None:
        return (1, float("inf"), vc)  # un-racked: always last
    desc = bool(member.rack and member.rack.desc_units)
    if desc:
        # U1 at top → ascending: smallest U first
        return (0, float(pos), vc)
    else:
        # U1 at bottom → descending: largest U first (negate)
        return (0, -float(pos), vc)


def panel_members(obj, user):
    """Return the devices of the panels of ``obj`` that ``user`` can view, in rack order."""
    if obj.virtual_chassis is None:
        return [obj]
    return sorted(
        obj.virtual_chassis.members.restrict(user, "view").select_related("rack"),
        key=_rack_sort_key,
    )


def viewable_device_view(device_type, user):
    """Return the DeviceView of ``device_type``; a DeviceView that ``user`` cannot view does not exist."""
    return DeviceView.objects.restrict(user, "view").get(device_type=device_type)


def _peer_label(peer):
    device = getattr(peer, "device", None)
    name = getattr(peer, "name", None)
    if device and name:
        return f"{device} | {name}"
    return str(peer)


def _peers(component):
    endpoints = getattr(component, "connected_endpoints", None)
    return list(endpoints or []) or list(component.link_peers)


def label_connections(components, user):
    """Set ``peer_labels`` and ``cable_color`` on each of ``components``.

    Only the peers and the cable that ``user`` can view count. ``cable_color``
    is None without a cable that ``user`` can view, and "" for a cable without
    a colour.
    """
    components = list(components)
    peers = [(component, _peers(component)) for component in components]
    pks_by_model = defaultdict(set)
    for _component, component_peers in peers:
        for peer in component_peers:
            pks_by_model[type(peer)].add(peer.pk)
    pks_by_model[Cable] = {c.cable_id for c in components if c.cable_id is not None}
    visible = {
        (model, pk)
        for model, pks in pks_by_model.items()
        for pk in model.objects.restrict(user, "view")
        .filter(pk__in=pks)
        .values_list("pk", flat=True)
    }
    for component, component_peers in peers:
        component.peer_labels = [
            _peer_label(peer)
            for peer in component_peers
            if (type(peer), peer.pk) in visible
        ]
        component.cable_color = (
            component.cable.color if (Cable, component.cable_id) in visible else None
        )


def _member_ports(member, ports_chassis, key, user):
    """Add the interfaces, console ports and console-server ports of ``member`` that ``user`` can view."""
    ports_chassis = process_interfaces(
        member.interfaces.restrict(user, "view"), ports_chassis, key
    )
    return process_console_ports(member, ports_chassis, key, user)


def _device_ports(obj, user):
    """Return the ports_chassis of a device that is not in a virtual chassis."""
    ports_chassis = process_interfaces(
        obj.interfaces.restrict(user, "view"), {}, obj.name
    )
    ports_chassis = process_ports(
        obj.frontports.restrict(user, "view"), ports_chassis, "Front"
    )
    ports_chassis = process_ports(
        obj.rearports.restrict(user, "view"), ports_chassis, "Rear"
    )
    return process_console_ports(obj, ports_chassis, obj.name, user)


def prepare(obj, user):
    """Return (dv, modules, ports_chassis, device_view) with the objects that ``user`` can view.

    Returns (None, None, None, None) if no DeviceView is configured for the
    device type.
    """
    ports_chassis = {}
    dv = {}
    modules = {}
    device_view = None

    try:
        if obj.virtual_chassis is None:
            device_view = viewable_device_view(obj.device_type, user)
            dv[1] = get_css_for_device_view(device_view)
            modules[1] = obj.modules.restrict(user, "view")
            ports_chassis = _device_ports(obj, user)
        else:
            for member in panel_members(obj, user):
                pos = member.vc_position
                member_view = viewable_device_view(member.device_type, user)
                member_css = get_css_for_device_view(member_view)
                dv[pos] = re.sub(
                    r"\.area\b",
                    f".area.d{pos}",
                    member_css,
                )
                modules[pos] = member.modules.restrict(user, "view")
                ports_chassis = _member_ports(member, ports_chassis, pos, user)
    except ObjectDoesNotExist:
        return None, None, None, None

    label_connections(chain.from_iterable(ports_chassis.values()), user)
    return dv, modules, ports_chassis, device_view


def prepare_svg(obj, user):
    """Return (svg_views, modules, ports_chassis, device_view) for SVG rendering.

    ``svg_views`` is a dict mapping position → SVG markup string.
    Like ``prepare()``, returns (None, None, None, None) when no DeviceView exists,
    and includes only the objects that ``user`` can view.

    For virtual chassis devices each member is rendered as its own SVG panel,
    keyed by vc_position — matching the ports_chassis key used by the template.
    """
    ports_chassis = {}
    svg_views = {}
    modules = {}
    device_view = None

    try:
        if obj.virtual_chassis is None:
            device_view = viewable_device_view(obj.device_type, user)
            member_modules = list(obj.modules.restrict(user, "view"))
            variant = _detect_variant(member_modules, device_view)
            svg_views[obj.name] = get_svg_for_device_view(
                device_view, variant_name=variant
            )
            modules[obj.name] = member_modules
            ports_chassis = _device_ports(obj, user)
        else:
            for member in panel_members(obj, user):
                pos = member.vc_position
                member_view = viewable_device_view(member.device_type, user)
                member_modules = list(member.modules.restrict(user, "view"))
                variant = _detect_variant(member_modules, member_view)
                svg_views[pos] = get_svg_for_device_view(
                    member_view, variant_name=variant
                )
                modules[pos] = member_modules
                ports_chassis = _member_ports(member, ports_chassis, pos, user)
                device_view = member_view
    except ObjectDoesNotExist:
        return None, None, None, None

    label_connections(chain.from_iterable(ports_chassis.values()), user)
    return svg_views, modules, ports_chassis, device_view


def panel_title(obj, user):
    """Return the virtual chassis name of ``obj`` when ``user`` can view it, else the device name."""
    if (
        obj.virtual_chassis_id is not None
        and VirtualChassis.objects.restrict(user, "view")
        .filter(pk=obj.virtual_chassis_id)
        .exists()
    ):
        return obj.virtual_chassis.name
    return obj.name


def device_view_context(obj, user):
    """Return the render context for the device view of ``obj`` that ``user`` sees, or None without one.

    ``layout_error`` holds the parse error of a stored layout that does not parse.
    """
    context = {
        "layout_error": None,
        "device_view": None,
        "dv": None,
        "svg_views": None,
        "svg_panels": [],
        "use_svg": False,
        "modules": None,
        "ports_chassis": None,
        "height": device_height_px(obj.device_type),
        "active_panel": active_panel_key(obj),
        "panel_title": panel_title(obj, user),
    }
    try:
        use_svg = viewable_device_view(obj.device_type, user).use_svg
    except DeviceView.DoesNotExist:
        use_svg = False

    try:
        if use_svg:
            svg_views, modules, ports_chassis, device_view = prepare_svg(obj, user)
            # prepare_svg() gives None when a virtual chassis member has no device view.
            use_svg = svg_views is not None
            context["svg_views"] = svg_views
        if not use_svg:
            dv, modules, ports_chassis, device_view = prepare(obj, user)
            context["dv"] = dv
    except LayoutParseError as exc:
        context["layout_error"] = str(exc)
        return context

    svg_views = context["svg_views"]
    if (
        (context["dv"] is None and svg_views is None)
        or modules is None
        or ports_chassis is None
    ):
        return None

    svg_panels = []
    if use_svg and svg_views and ports_chassis:
        svg_panels = [
            (pos, svg_views[pos]) for pos in ports_chassis if pos in svg_views
        ]
        # Patch panel ports have the keys "Front" and "Rear", not the device name.
        if not svg_panels:
            svg_panels = list(svg_views.items())

    context.update(
        device_view=device_view,
        svg_panels=svg_panels,
        use_svg=use_svg,
        modules=modules,
        ports_chassis=ports_chassis,
    )
    return context
