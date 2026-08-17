from dcim.models import DeviceType
from django.utils.translation import gettext_lazy as _
from netbox.forms import NetBoxModelForm, NetBoxModelImportForm
from utilities.forms.fields import CSVModelChoiceField

from .models import DeviceView


class DeviceViewForm(NetBoxModelForm):
    class Meta:
        model = DeviceView
        fields = ("device_type", "grid_template_area", "yaml_layout", "render_mode")


class DeviceViewImportForm(NetBoxModelImportForm):
    device_type = CSVModelChoiceField(
        queryset=DeviceType.objects.all(),
        to_field_name="model",
        help_text=_("Device Model Name"),
    )

    class Meta:
        model = DeviceView
        fields = ("device_type", "grid_template_area", "yaml_layout", "render_mode")
