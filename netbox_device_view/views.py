from netbox.views import generic
from dcim.models import Device
from . import forms, models, tables, filtersets
from utilities.views import ViewTab, register_model_view
from .utils import prepare
import pprint

class DeviceViewView(generic.ObjectView):
    queryset = models.DeviceView.objects


class DeviceViewListView(generic.ObjectListView):
    queryset = models.DeviceView.objects
    table = tables.DeviceViewTable


class DeviceViewEditView(generic.ObjectEditView):
    queryset = models.DeviceView.objects
    form = forms.DeviceViewForm


class DeviceViewDeleteView(generic.ObjectDeleteView):
    queryset = models.DeviceView.objects

@register_model_view(Device, 'deviceview', path='device-view')
class DeviceDeviceView(generic.ObjectView):
    queryset=models.DeviceView.objects
    
    tab = ViewTab(
        label='Device View',
        badge=lambda obj: models.DeviceView.objects.filter(device_type=obj.device_type).count(),
        hide_if_empty=True
    )
    def get_extra_context(self, request, instance):
        dv, modules, ports_chassis = prepare(instance)
        return {
            'device_view': models.DeviceView.objects.filter(device_type=instance.device_type).first(),
            'dv': dv,
            'moduels': modules,
            'ports_chassis': ports_chassis
            }
    
    def get_object(self, **kwargs):
        return Device.objects.get(pk=kwargs.get('pk'))