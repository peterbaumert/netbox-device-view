import django_tables2 as tables

from netbox.tables import NetBoxTable, ChoiceFieldColumn
from .models import DeviceView


class DeviceViewTable(NetBoxTable):
    class Meta(NetBoxTable.Meta):
        model = DeviceView
        fields = ("pk", "id", "device_type", "grid_template_area")
        default_columns = ("device_type", "grid_template_area")
