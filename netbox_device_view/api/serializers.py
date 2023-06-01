from rest_framework import serializers

from netbox.api.serializers import NetBoxModelSerializer
from ..models import DeviceView


class DeviceViewSerializer(NetBoxModelSerializer):
    class Meta:
        model = DeviceView
        fields = (
            "id",
            "display",
            "device_type",
            "grid_template_area",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
