import django_filters
from dcim.models import DeviceType, Manufacturer
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from netbox.filtersets import NetBoxModelFilterSet
from utilities.filtersets import register_filterset

from .models import DeviceView


@register_filterset
class DeviceViewFilterSet(NetBoxModelFilterSet):
    manufacturer_id = django_filters.ModelMultipleChoiceFilter(
        field_name="device_type__manufacturer",
        queryset=Manufacturer.objects.all(),
        label=_("Manufacturer (ID)"),
    )
    manufacturer = django_filters.ModelMultipleChoiceFilter(
        field_name="device_type__manufacturer__slug",
        queryset=Manufacturer.objects.all(),
        to_field_name="slug",
        label=_("Manufacturer (slug)"),
    )
    device_type_id = django_filters.ModelMultipleChoiceFilter(
        queryset=DeviceType.objects.all(),
        label=_("Device type (ID)"),
    )
    device_type = django_filters.ModelMultipleChoiceFilter(
        field_name="device_type__slug",
        queryset=DeviceType.objects.all(),
        to_field_name="slug",
        label=_("Device type (slug)"),
    )

    class Meta:
        model = DeviceView
        fields = ("id",)

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(device_type__model__icontains=value)
            | Q(device_type__manufacturer__name__icontains=value)
        )
