from dcim.models import DeviceType
from django import forms
from django.utils.translation import gettext_lazy as _
from netbox.forms import NetBoxModelForm, NetBoxModelImportForm
from utilities.forms.fields import CSVModelChoiceField, DynamicModelChoiceField

from . import datasource_import
from .models import DeviceView
from .utils import normalize_grid_template_area


class SanitizeGridTemplateAreaMixin:
    """Normalize the whitespace of grid_template_area before validation."""

    def clean_grid_template_area(self):
        value = self.cleaned_data.get("grid_template_area", "")
        if not value:
            return value
        return normalize_grid_template_area(value)


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


class DataSourceImportSourceForm(forms.Form):
    source = forms.ModelChoiceField(
        queryset=None,
        label=_("Data Source"),
        help_text=_("Only synchronized Data Sources are listed."),
    )
    prefix = forms.CharField(
        required=False,
        initial="examples/",
        label=_("Directory prefix"),
        help_text=_("Path relative to the source root. Leave empty for the root."),
    )
    q = forms.CharField(required=False, label=_("Search paths"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].queryset = datasource_import.importable_sources(user)

    def clean_prefix(self):
        return datasource_import.normalize_prefix(self.cleaned_data["prefix"])


class DataSourceImportTargetForm(forms.Form):
    """One device type selector and one Replace choice for each selected DataFile."""

    def __init__(self, *args, user, datafiles, catalog_rows=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.datafiles = list(datafiles)
        for datafile in self.datafiles:
            self.fields[self.field_name(datafile.pk)] = DynamicModelChoiceField(
                queryset=datasource_import.permitted_device_types(user),
                label=_("Device type"),
            )
            self.fields[self.replace_name(datafile.pk)] = forms.BooleanField(
                required=False, label=_("Replace an existing device view")
            )
        for row in catalog_rows:
            if row.is_selectable:
                self.initial[self.field_name(row.datafile.pk)] = row.suggestion.pk

    @staticmethod
    def field_name(datafile_id):
        return f"device_type_{datafile_id}"

    @staticmethod
    def replace_name(datafile_id):
        return f"replace_{datafile_id}"

    def rows(self):
        return [
            (
                datafile,
                self[self.field_name(datafile.pk)],
                self[self.replace_name(datafile.pk)],
            )
            for datafile in self.datafiles
        ]

    def selections(self):
        return [
            datasource_import.Selection(
                datafile_id=datafile.pk,
                device_type_id=self.cleaned_data[self.field_name(datafile.pk)].pk,
                replace=self.cleaned_data[self.replace_name(datafile.pk)],
            )
            for datafile in self.datafiles
        ]
