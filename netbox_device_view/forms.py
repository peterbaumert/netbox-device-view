from netbox.forms import NetBoxModelForm
from .models import DeviceView


class DeviceViewForm(NetBoxModelForm):
    class Meta:
        model = DeviceView
        fields = ("device_type", "grid_template_area")
