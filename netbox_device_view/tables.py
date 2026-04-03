import django_tables2 as tables

from netbox.tables import NetBoxTable

from .models import DeviceView


class DeviceViewTable(NetBoxTable):
    grid_template_area = tables.Column(
        verbose_name="Layout",
        orderable=False,
        attrs={
            "td": {
                "style": (
                    "max-width:300px; overflow:hidden; "
                    "text-overflow:ellipsis; white-space:nowrap;"
                )
            }
        },
    )

    class Meta(NetBoxTable.Meta):
        model = DeviceView
        fields = ("pk", "id", "device_type", "grid_template_area")
        default_columns = ("device_type", "grid_template_area")
