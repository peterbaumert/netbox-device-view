from extras.plugins import PluginTemplateExtension
from .models import DeviceView
import re


class Ports(PluginTemplateExtension):
    def page(self):
        obj = self.context["object"]
        request = self.context["request"]
        url = request.build_absolute_uri(obj.get_absolute_url())
        try:
            dv = DeviceView.objects.get(device_type=obj.device_type)
        except:
            return ""

        interfaces = obj.vc_interfaces()
        ports_chassis = {}

        for inte in interfaces:
            regex = r"^(?P<type>([a-z]+))((?P<switch>[0-9]+)\/)?((?P<module>[0-9]+)\/)?((?P<port>[0-9]+))$"
            matches = re.search(regex, inte.name.lower())
            if matches:
                inte.stylename = (
                    (matches["type"] or "")
                    + (matches["module"] or "")
                    + "-"
                    + matches["port"]
                )
                sw = int(matches["switch"] or 0)
                if sw not in ports_chassis and sw != 0:
                    ports_chassis[sw] = []
                if sw != 0:
                    ports_chassis[sw].append(inte)

        return self.render(
            "netbox_device_view/ports.html",
            extra_context={"ports_chassis": ports_chassis, "dv": dv},
        )


class DevicePorts(Ports):
    model = "dcim.device"

    def full_width_page(self):
        return self.page()


template_extensions = [DevicePorts]
