from dcim.models import Device
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from netbox.views import generic
from netbox.views.generic import BulkImportView
from utilities.views import ViewTab, register_model_view

from netbox_device_view.forms import DeviceViewImportForm

from . import forms, models, tables
from .utils import (
    device_height_px,
    get_stylenames_for_device_type,
    prepare,
    prepare_svg,
)


class DeviceViewView(generic.ObjectView):
    queryset = models.DeviceView.objects


class DeviceViewListView(generic.ObjectListView):
    queryset = models.DeviceView.objects.annotate(
        device_count=Count("device_type__instances")
    )
    table = tables.DeviceViewTable


class DeviceViewEditView(generic.ObjectEditView):
    queryset = models.DeviceView.objects
    form = forms.DeviceViewForm
    template_name = "netbox_device_view/deviceview_edit.html"


@method_decorator(login_required, name="dispatch")
class DeviceTypeInterfacesView(View):
    """Return JSON list of component templates with their CSS stylenames for
    the given device type.  Used by the Add/Edit form to preview grid-area
    names before saving."""

    def get(self, request):
        device_type_id = request.GET.get("device_type_id", "").strip()
        if not device_type_id or not device_type_id.isdigit():
            return JsonResponse([], safe=False)
        return JsonResponse(
            get_stylenames_for_device_type(int(device_type_id)), safe=False
        )


class DeviceViewBulkImportView(BulkImportView):
    queryset = models.DeviceView.objects.all()
    model_form = DeviceViewImportForm


class DeviceViewDeleteView(generic.ObjectDeleteView):
    queryset = models.DeviceView.objects


@register_model_view(Device, "deviceview", path="device-view")
class DeviceDeviceView(generic.ObjectView):
    queryset = models.DeviceView.objects

    tab = ViewTab(
        label="Device View",
        badge=lambda obj: models.DeviceView.objects.filter(
            device_type=obj.device_type
        ).count(),
        hide_if_empty=True,
    )

    def get_extra_context(self, request, instance):
        height = device_height_px(instance.device_type)

        # Determine render mode from the device view record (if it exists)
        try:
            device_view_check = models.DeviceView.objects.get(
                device_type=instance.device_type
            )
            use_svg = device_view_check.use_svg
        except models.DeviceView.DoesNotExist:
            use_svg = False

        # Initialise all context variables to safe defaults
        dv = None
        svg_views = None
        modules = None
        ports_chassis = None
        device_view = None

        if use_svg:
            svg_views, modules, ports_chassis, device_view = prepare_svg(instance)
            if svg_views is None:
                # SVG preparation failed (e.g. virtual chassis) — fall back to CSS
                use_svg = False

        if not use_svg:
            dv, modules, ports_chassis, device_view = prepare(instance)

        # Build an ordered list of (pos, svg_markup).
        # For standard devices svg_views is keyed by device name matching
        # ports_chassis.  For patch panels ports are split into "Front"/"Rear"
        # keys that don't exist in svg_views, so we fall back to rendering all
        # svg_views entries directly (the SVG already contains both faces).
        svg_panels = []
        if use_svg and svg_views and ports_chassis:
            for pos in ports_chassis:
                if pos in svg_views:
                    svg_panels.append((pos, svg_views[pos]))
            # Fallback: no panel matched (e.g. patch panel with Front/Rear port keys)
            # — render all svg_views entries once
            if not svg_panels:
                for pos, markup in svg_views.items():
                    svg_panels.append((pos, markup))

        return {
            "device_view": device_view,
            "dv": dv,
            "svg_views": svg_views,
            "svg_panels": svg_panels,
            "use_svg": use_svg,
            "modules": modules,
            "height": height,
            "ports_chassis": ports_chassis,
            "cable_colors": request.GET.get("cable_colors", "off"),
        }

    def get_object(self, **kwargs):
        return Device.objects.get(pk=kwargs.get("pk"))
