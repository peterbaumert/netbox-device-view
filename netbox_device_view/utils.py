from dcim.models import ConsolePort
from .models import DeviceView
from django.core.exceptions import ObjectDoesNotExist

import re


# --- process_interfaces: With fix for '/' names, NO prefixing ---
def process_interfaces(interfaces, ports_chassis, dev):
    if interfaces is not None:
        for itf in interfaces:
            # Skip virtual/lag interfaces
            if itf.type == "virtual" or itf.type == "lag":
                continue

            # --- Generate base stylename (Handles '/' correctly) ---
            if "/" in itf.name:
                itf.stylename = re.sub(r"[^.a-zA-Z\d]", "-", itf.name.lower())
            else:
                # Original regex logic for other name formats
                regex = r"^(?P<type>([a-zA-Z\-_]*))(\/|(?P<dev>[0-9]+).|\s)?((?P<module>[0-9]+).|\s)?((?P<port>[0-9]+))$"
                matches = re.search(regex, itf.name.lower())
                if matches:
                    itf.stylename = (
                        (matches["type"] or "")
                        + (matches["module"] or "")
                        + "-"
                        + matches["port"]
                    )
                else:
                    # Fallback if regex fails for non-'/' names
                    itf.stylename = re.sub(r"[^.a-zA-Z\d]", "-", itf.name.lower())
            # --- Base stylename generated ---

            # Original purely numeric prefix logic (can be left or removed)
            # This does not affect names like '1-1-1'
            if (str(itf.stylename))[0].isdigit():
                stylename_str=str(itf.stylename)
                if stylename_str and stylename_str[0].isdigit():
                    itf.stylename = f"p{itf.stylename}" 

            # Add to dictionary
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
            # Use simple substitution for stylename
            port.stylename = re.sub(r"[^.a-zA-Z\d]", "-", port.name.lower())
            # Prefix if purely numeric
            if str(port.stylename).isdigit():
                port.stylename = f"p{port.stylename}"
            # Add to dictionary
            if dev not in ports_chassis:
                ports_chassis[dev] = []
            ports_chassis[dev].append(port)
    return ports_chassis


# --- prepare: With fix for consistent key '1' for standalone ---
def prepare(obj):
    ports_chassis = {}
    dv = {}
    modules = {}
    try:
        # Check if standalone device
        if obj.virtual_chassis is None: 
            # Fetch DeviceView CSS definition
            dv[1] = DeviceView.objects.get(device_type=obj.device_type).grid_template_area
            # Get modules
            modules[1] = obj.modules.all()
            # Process ports using consistent key '1'
            ports_chassis = process_interfaces(obj.interfaces.all(), ports_chassis, 1)
            ports_chassis = process_ports(obj.frontports.all(), ports_chassis, 1) # Use key 1
            ports_chassis = process_ports(obj.rearports.all(), ports_chassis, 1)  # Use key 1
            ports_chassis = process_ports(
                ConsolePort.objects.filter(device_id=obj.id), ports_chassis, 1 # Use key 1
            )
        # Else handle Virtual Chassis
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
    # Handle case where DeviceView definition doesn't exist
    except ObjectDoesNotExist:
        return None, None, None
        
    return dv, modules, ports_chassis
