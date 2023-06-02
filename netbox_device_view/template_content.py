from extras.plugins import PluginTemplateExtension
from .models import DeviceView
import re


class Ports(PluginTemplateExtension):
    def page(self):
        ports_chassis = {}
        dv = {}
        modules = {}

        obj = self.context["object"]
        request = self.context["request"]
        url = request.build_absolute_uri(obj.get_absolute_url())
        try:
            if obj.virtual_chassis is None:
                dv[1] = DeviceView.objects.get(
                    device_type=obj.device_type
                ).grid_template_area.replace(".area", ".area1")
                modules[1] = obj.modules.all()
            else:
                for member in obj.virtual_chassis.members.all():
                    dv[member.vc_position] = DeviceView.objects.get(
                        device_type=member.device_type
                    ).grid_template_area.replace(
                        ".area", ".area" + str(member.vc_position)
                    )
                    modules[member.vc_position] = member.modules.all()
        except:
            return ""

        interfaces = obj.vc_interfaces()

        for itf in interfaces:
            regex = r"^(?P<type>([a-z]+))((?P<switch>[0-9]+)\/)?((?P<module>[0-9]+)\/)?((?P<port>[0-9]+))$"
            matches = re.search(regex, itf.name.lower())
            if matches:
                itf.stylename = (
                    (matches["type"] or "")
                    + (matches["module"] or "")
                    + "-"
                    + matches["port"]
                )
                sw = int(matches["switch"] or 0)
                if sw not in ports_chassis and sw != 0:
                    ports_chassis[sw] = []
                if sw != 0:
                    ports_chassis[sw].append(itf)

        return self.render(
            "netbox_device_view/ports.html",
            extra_context={
                "ports_chassis": ports_chassis,
                "dv": dv,
                "modules": modules,
            },
        )


class DevicePorts(Ports):
    model = "dcim.device"

    def full_width_page(self):
        return self.page()


template_extensions = [DevicePorts]
