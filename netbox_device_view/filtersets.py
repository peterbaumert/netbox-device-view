from netbox.filtersets import NetBoxModelFilterSet
from .models import DeviceView


class DeviceViewFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = DeviceView
        fields = ("id", "device_type")
