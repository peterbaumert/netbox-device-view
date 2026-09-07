import unicodedata

from dcim.models import DeviceType
from django.utils.translation import gettext_lazy as _
from netbox.forms import NetBoxModelForm, NetBoxModelImportForm
from utilities.forms.fields import CSVModelChoiceField

from .models import DeviceView


class SanitizeGridTemplateAreaMixin:
    """
    Normalize non-standard whitespace characters in the grid_template_area
    field before validation/save.

    The CSS `grid-template-areas` property is sensitive to the exact
    whitespace used to separate tokens within each quoted row string.
    Pasting layout definitions from documentation, examples, or other
    sources (especially from rendered web pages or word processors) can
    silently introduce non-breaking spaces (U+00A0) and other Unicode
    space separators that look identical to a normal space in the NetBox
    UI's text field but are invalid as `grid-template-areas` separators.

    This normalizes every Unicode "space separator" (category Zs) to a
    regular ASCII space, while leaving newlines untouched so multi-row
    definitions keep their line structure.
    """

    def clean_grid_template_area(self):
        value = self.cleaned_data.get("grid_template_area", "")
        if not value:
            return value
        return "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in value)


class DeviceViewForm(SanitizeGridTemplateAreaMixin, NetBoxModelForm):
    class Meta:
        model = DeviceView
        fields = ("device_type", "grid_template_area", "yaml_layout", "render_mode")


class DeviceViewImportForm(SanitizeGridTemplateAreaMixin, NetBoxModelImportForm):
    device_type = CSVModelChoiceField(
        queryset=DeviceType.objects.all(),
        to_field_name="model",
        help_text=_("Device Model Name"),
    )

    class Meta:
        model = DeviceView
        fields = ("device_type", "grid_template_area", "yaml_layout", "render_mode")
