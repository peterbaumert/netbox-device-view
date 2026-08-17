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
            "yaml_layout",
            "render_mode",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
