from dcim.models import Device
from django.conf import settings
from netbox.plugins import PluginTemplateExtension

from .utils import device_height_px, prepare, prepare_svg


class Ports(PluginTemplateExtension):
    def page(self):
        obj = self.context["object"]

        if not isinstance(obj, Device):
            return ""

        height = device_height_px(obj.device_type)

        # Check render mode
        from .models import DeviceView

        try:
            dv_record = DeviceView.objects.get(device_type=obj.device_type)
            use_svg = dv_record.use_svg
        except DeviceView.DoesNotExist:
            use_svg = False

        # Initialise all context variables to safe defaults
        dv = None
        svg_views = None
        modules = None
        ports_chassis = None

        if use_svg:
            svg_views, modules, ports_chassis, _ = prepare_svg(obj)
            if svg_views is None:
                # Fallback if SVG preparation fails
                use_svg = False

        if not use_svg:
            dv, modules, ports_chassis, _ = prepare(obj)

        if (
            (dv is None and svg_views is None)
            or modules is None
            or ports_chassis is None
        ):
            return ""

        # Build svg_panels with fallback for patch panels whose ports_chassis
        # keys ("Front"/"Rear") don't match svg_views keys (device name).
        svg_panels = []
        if use_svg and svg_views and ports_chassis:
            for pos in ports_chassis:
                if pos in svg_views:
                    svg_panels.append((pos, svg_views[pos]))
            if not svg_panels:
                for pos, markup in svg_views.items():
                    svg_panels.append((pos, markup))

        return self.render(
            "netbox_device_view/ports.html",
            extra_context={
                "dv": dv,
                "svg_views": svg_views,
                "svg_panels": svg_panels,
                "use_svg": use_svg,
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
