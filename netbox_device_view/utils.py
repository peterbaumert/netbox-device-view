from dcim.models import ConsolePort
from .models import DeviceView
from django.core.exceptions import ObjectDoesNotExist

import re


def process_interfaces(interfaces, ports_chassis, dev):
    if interfaces is not None:
        for itf in interfaces:
            if itf.type == "virtual" or itf.type == "lag":
                continue
            # Convert to lowercase and replace common separators with hyphens
            # This replaces the original regex matching and if/else block
            stylename = re.sub(r"[/\.\s]+", "-", itf.name.lower())

            # Clean up multiple hyphens and leading/trailing hyphens
            stylename = re.sub(r"-+", "-", stylename).strip("-")

            # If the name becomes empty after cleaning, use a fallback
            if not stylename:
                stylename = f"iface-{itf.pk}"  # Use a unique fallback

            # Assign the generated stylename back to the object property
            itf.stylename = stylename

            # Check if the stylename exists and starts with a digit or a hyphen
            # This replaces the original 'if itf.stylename.isdigit():' line
            if itf.stylename and (
                itf.stylename[0].isdigit() or itf.stylename[0] == "-"
            ):
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


def prepare(obj):
    ports_chassis = {}
    dv = {}
    modules = {}

    try:
        if obj.virtual_chassis is None:
            dv[1] = DeviceView.objects.get(
                device_type=obj.device_type
            ).grid_template_area
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
            for member in obj.virtual_chassis.members.all():
                dv[member.vc_position] = DeviceView.objects.get(
                    device_type=member.device_type
                ).grid_template_area.replace(
                    ".area", ".area.d" + str(member.vc_position)
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
        return None, None, None

    return dv, modules, ports_chassis
