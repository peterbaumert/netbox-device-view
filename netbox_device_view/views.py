from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View

from netbox.views import generic
from dcim.models import Device
from . import forms, models, tables
from utilities.views import ViewTab, register_model_view
from .utils import device_height_px, get_stylenames_for_device_type, prepare

from netbox.views.generic import BulkImportView
from netbox_device_view.forms import DeviceViewImportForm


class DeviceViewView(generic.ObjectView):
    queryset = models.DeviceView.objects


class DeviceViewListView(generic.ObjectListView):
    queryset = models.DeviceView.objects
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
        dv, modules, ports_chassis, device_view = prepare(instance)
        height = device_height_px(instance.device_type)
        return {
            "device_view": device_view,
            "dv": dv,
            "modules": modules,
            "height": height,
            "ports_chassis": ports_chassis,
            "cable_colors": request.GET.get("cable_colors", "off"),
        }

    def get_object(self, **kwargs):
        return Device.objects.get(pk=kwargs.get("pk"))
