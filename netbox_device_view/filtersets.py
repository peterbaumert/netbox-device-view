from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet
from utilities.filtersets import register_filterset

from .models import DeviceView


@register_filterset
class DeviceViewFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = DeviceView
        fields = ("id", "device_type")

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(device_type__model__icontains=value)
            | Q(device_type__manufacturer__name__icontains=value)
        )
