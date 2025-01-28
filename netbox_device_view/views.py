from netbox.views import generic
from dcim.models import Device
from . import forms, models, tables, filtersets
from utilities.views import ViewTab, register_model_view
from .utils import prepare
from django.http import HttpResponse
import pprint

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
        dv, modules, ports_chassis = prepare(instance)
        height = (
            instance.device_type.u_height * 2 * 20 + instance.device_type.u_height * 2
        )
        return {
            "device_view": models.DeviceView.objects.filter(
                device_type=instance.device_type
            ).first(),
            "dv": dv,
            "modules": modules,
            "height": height,
            "ports_chassis": ports_chassis,
            "cable_colors": request.GET.get("cable_colors", "off"),
            "something_else": request.GET.get("something_else", "off"),
        }

    def get_object(self, **kwargs):
        return Device.objects.get(pk=kwargs.get("pk"))
