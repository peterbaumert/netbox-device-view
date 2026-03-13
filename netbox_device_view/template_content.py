from netbox.plugins import PluginTemplateExtension
from .utils import device_height_px, prepare
from django.conf import settings
from dcim.models import Device


class Ports(PluginTemplateExtension):
    def page(self):
        obj = self.context["object"]

        if not isinstance(obj, Device):
            return ""

        dv, modules, ports_chassis, _ = prepare(obj)

        height = device_height_px(obj.device_type)

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
        cfg = settings.PLUGINS_CONFIG["netbox_device_view"]
        if not cfg["show_on_device_tab"]:
            return ""
        if cfg.get("device_tab_position", "bottom") != "bottom":
            return ""
        return self.page()

    def alerts(self):
        cfg = settings.PLUGINS_CONFIG["netbox_device_view"]
        if not cfg["show_on_device_tab"]:
            return ""
        if cfg.get("device_tab_position", "bottom") != "top":
            return ""
        # Only render on the main device tab — all other tabs have a different URL path
        obj = self.context["object"]
        if self.context["request"].path != obj.get_absolute_url():
            return ""
        return self.page()


template_extensions = [DevicePorts]
