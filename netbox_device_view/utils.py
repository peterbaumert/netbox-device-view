from dcim.models import ConsolePort
from .models import DeviceView
from django.core.exceptions import ObjectDoesNotExist

import re


def process_interfaces(interfaces, ports_chassis, dev=1):
    if interfaces is not None:
        for itf in interfaces:
            regex = r"^(?P<type>([a-z]+))((?P<dev>[0-9]+)\/)?((?P<module>[0-9]+)\/)?((?P<port>[0-9]+))$"
            matches = re.search(regex, itf.name.lower())
            if matches:
                itf.stylename = (
                    (matches["type"] or "")
                    + (matches["module"] or "")
                    + "-"
                    + matches["port"]
                )
                sw = int(matches["dev"] or 999)
                if (
                    hasattr(itf, "mgmt_only")
                    and itf.mgmt_only
                    and itf.type != "virtual"
                ) or hasattr(itf, "mgmt_only") == False:
                    sw = dev
                if sw not in ports_chassis and sw != 999:
                    ports_chassis[sw] = []
                if sw != 999:
                    ports_chassis[sw].append(itf)
    return ports_chassis


def process_ports(ports, ports_chassis, where):
    if ports is not None:
        for port in ports:
            regex = r"^(?P<type>([A-Za-z]+))[\s]?((?P<port>[0-9]+))$"
            matches = re.search(regex, port.name.lower())
            port.is_port = True
            if matches:
                port.stylename = (matches["type"] or "") + "-" + matches["port"]
            else:
                port.stylename = re.sub(r"[^.a-zA-Z\d]", "-", port.name.lower())
            sw = where
            if port.type == "virtual":
                sw = 999
            if sw not in ports_chassis and sw != 999:
                ports_chassis[sw] = []
            if sw != 999:
                ports_chassis[sw].append(port)
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
            ports_chassis = process_interfaces(obj.interfaces.all(), ports_chassis)
            ports_chassis = process_ports(obj.frontports.all(), ports_chassis, "Front")
            ports_chassis = process_ports(obj.rearports.all(), ports_chassis, "Rear")
            ports_chassis = process_ports(
                ConsolePort.objects.filter(device_id=obj.id),
                ports_chassis,
                list(ports_chassis.keys())[0],
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
