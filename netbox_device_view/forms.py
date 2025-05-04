from netbox.forms import NetBoxModelForm, NetBoxModelImportForm
from django.utils.translation import gettext_lazy as _
from .models import DeviceView
from dcim.models import DeviceType
from utilities.forms.fields import CSVModelChoiceField
import re


class DeviceViewForm(NetBoxModelForm):
    class Meta:
        model = DeviceView
        fields = ("device_type", "grid_template_area")

    def clean_grid_template_area(self):
        """
        Cleans the grid_template_area field input, replacing common
        non-standard whitespace characters with standard spaces.
        """
        # Get the data from the cleaned_data dictionary
        grid_string = self.cleaned_data["grid_template_area"]

        if grid_string:  # Ensure the string is not empty before processing
            # Replace Non-Breaking Spaces (U+00A0) with standard spaces
            cleaned_string = grid_string.replace("\u00a0", " ")

            # Add replacements for other problematic unicode whitespaces.
            cleaned_string = cleaned_string.replace("\u2005", " ")  # Four-per-em space

            # Optional: You could add further standardization here, e.g.,
            # replacing any sequence of whitespace with a single space:
            # cleaned_string = re.sub(r'\s+', ' ', cleaned_string).strip()
            # But be cautious this doesn't alter intended structure if internal
            # newlines/spacing within quotes were significant in the design.
            # Simple replacement of specific characters is safer if unsure.

        else:
            cleaned_string = grid_string  # Keep empty string if input was empty

        # Return the cleaned value
        return cleaned_string


class DeviceViewImportForm(NetBoxModelImportForm):
    device_type = CSVModelChoiceField(
        queryset=DeviceType.objects.all(),
        to_field_name="model",
        help_text=_("Device Model Name"),
    )

    class Meta:
        model = DeviceView
        fields = ("device_type", "grid_template_area")
