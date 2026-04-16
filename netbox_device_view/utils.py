import re

from django.core.exceptions import ObjectDoesNotExist

from dcim.models import (
    ConsolePort,
    ConsolePortTemplate,
    FrontPortTemplate,
    InterfaceTemplate,
    RearPortTemplate,
)

from .layout import (
    get_css_for_device_view,
    get_svg_for_device_view,
    parse as parse_layout,
)
from .models import DeviceView


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


def prepare(obj):
    """Return (dv, modules, ports_chassis, device_view) for the given device.

    Returns (None, None, None, None) if no DeviceView is configured for the
    device type.
    """
    ports_chassis = {}
    dv = {}
    modules = {}
    device_view = None

    try:
        if obj.virtual_chassis is None:
            device_view = DeviceView.objects.get(device_type=obj.device_type)
            dv[1] = get_css_for_device_view(device_view)
            modules[1] = obj.modules.all()
            ports_chassis = process_interfaces(
                obj.interfaces.all(), ports_chassis, obj.name
            )
            ports_chassis = process_ports(obj.frontports.all(), ports_chassis, "Front")
            ports_chassis = process_ports(obj.rearports.all(), ports_chassis, "Rear")
            ports_chassis = process_ports(
                ConsolePort.objects.filter(device_id=obj.id),
                ports_chassis,
                obj.name,
            )
        else:
            members = sorted(
                obj.virtual_chassis.members.select_related("rack").all(),
                key=_rack_sort_key,
            )
            for member in members:
                pos = member.vc_position
                member_view = DeviceView.objects.get(device_type=member.device_type)
                member_css = get_css_for_device_view(member_view)
                dv[pos] = re.sub(
                    r"\.area\b",
                    f".area.d{pos}",
                    member_css,
                )
                modules[member.vc_position] = member.modules.all()
                ports_chassis = process_interfaces(
                    member.interfaces.all(), ports_chassis, member.vc_position
                )
                ports_chassis = process_ports(
                    ConsolePort.objects.filter(device_id=member.id),
                    ports_chassis,
                    member.vc_position,
                )
    except ObjectDoesNotExist:
        return None, None, None, None

    return dv, modules, ports_chassis, device_view


def prepare_svg(obj):
    """Return (svg_views, modules, ports_chassis, device_view) for SVG rendering.

    ``svg_views`` is a dict mapping position → SVG markup string.
    Like ``prepare()``, returns (None, None, None, None) when no DeviceView exists.

    For virtual chassis devices each member is rendered as its own SVG panel,
    keyed by vc_position — matching the ports_chassis key used by the template.
    """
    ports_chassis = {}
    svg_views = {}
    modules = {}
    device_view = None

    try:
        if obj.virtual_chassis is None:
            device_view = DeviceView.objects.get(device_type=obj.device_type)
            member_modules = list(obj.modules.all())
            variant = _detect_variant(member_modules, device_view)
            svg_views[obj.name] = get_svg_for_device_view(
                device_view, variant_name=variant
            )
            modules[obj.name] = member_modules
            ports_chassis = process_interfaces(
                obj.interfaces.all(), ports_chassis, obj.name
            )
            ports_chassis = process_ports(obj.frontports.all(), ports_chassis, "Front")
            ports_chassis = process_ports(obj.rearports.all(), ports_chassis, "Rear")
            ports_chassis = process_ports(
                ConsolePort.objects.filter(device_id=obj.id),
                ports_chassis,
                obj.name,
            )
        else:
            members = sorted(
                obj.virtual_chassis.members.select_related("rack").all(),
                key=_rack_sort_key,
            )
            for member in members:
                pos = member.vc_position
                member_view = DeviceView.objects.get(device_type=member.device_type)
                member_modules = list(member.modules.all())
                variant = _detect_variant(member_modules, member_view)
                svg_views[pos] = get_svg_for_device_view(
                    member_view, variant_name=variant
                )
                modules[pos] = member_modules
                ports_chassis = process_interfaces(
                    member.interfaces.all(), ports_chassis, pos
                )
                ports_chassis = process_ports(
                    ConsolePort.objects.filter(device_id=member.id),
                    ports_chassis,
                    pos,
                )
                device_view = member_view
    except ObjectDoesNotExist:
        return None, None, None, None

    return svg_views, modules, ports_chassis, device_view
