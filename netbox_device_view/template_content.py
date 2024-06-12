from netbox.plugins import PluginTemplateExtension
from .utils import prepare
from django.conf import settings


class Ports(PluginTemplateExtension):
    def page(self):
        obj = self.context["object"]
        request = self.context["request"]
        url = request.build_absolute_uri(obj.get_absolute_url())

        dv, modules, ports_chassis = prepare(obj)

        height = obj.device_type.u_height * 2 * 20 + obj.device_type.u_height * 2

        if dv is None or modules is None or ports_chassis is None:
            return ""

        return self.render(
            "netbox_device_view/ports.html",
            extra_context={
                "dv": dv,
                "modules": modules,
                "height": height,
                "ports_chassis": ports_chassis,
            },
        )


class DevicePorts(Ports):
    model = "dcim.device"

    def full_width_page(self):
        if settings.PLUGINS_CONFIG["netbox_device_view"]["show_on_device_tab"] == False:
            return ""
        return self.page()


template_extensions = [DevicePorts]
