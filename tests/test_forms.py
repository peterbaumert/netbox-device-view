"""
Tests for SanitizeGridTemplateAreaMixin (issue #43): non-standard
whitespace characters (e.g. non-breaking space, U+00A0) pasted into the
Grid Template Area field must be normalized to regular ASCII spaces
before the CSS grid-template-areas value is saved, since such
characters are invalid as row-token separators but are visually
indistinguishable from a normal space in the NetBox UI.
"""

from dcim.models import DeviceType, Manufacturer
from django.test import TestCase

from netbox_device_view.forms import DeviceViewForm


class SanitizeGridTemplateAreaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name="Cisco", slug="cisco")
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="C9300-24T"
        )

    def _form_data(self, grid_template_area):
        return {
            "device_type": self.device_type.pk,
            "grid_template_area": grid_template_area,
            "yaml_layout": "",
            "render_mode": "css",
        }

    def test_non_breaking_space_normalized_to_regular_space(self):
        # U+00A0 non-breaking space between tokens
        raw = '.deviceview.area{grid-template-areas:"a\u00a0b\u00a0c";}'
        form = DeviceViewForm(data=self._form_data(raw))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("\u00a0", form.cleaned_data["grid_template_area"])
        self.assertIn("a b c", form.cleaned_data["grid_template_area"])

    def test_regular_input_unchanged(self):
        raw = '.deviceview.area{grid-template-areas:"a b c";}'
        form = DeviceViewForm(data=self._form_data(raw))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["grid_template_area"], raw)

    def test_newlines_preserved(self):
        raw = '.deviceview.area{\ngrid-template-areas:\n"a b"\n"c d";\n}'
        form = DeviceViewForm(data=self._form_data(raw))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["grid_template_area"].count("\n"), 4)

    def test_multiple_unicode_space_variants_normalized(self):
        # U+2003 EM SPACE and U+2009 THIN SPACE are also category Zs
        raw = '.deviceview.area{grid-template-areas:"a\u2003b\u2009c";}'
        form = DeviceViewForm(data=self._form_data(raw))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn("a b c", form.cleaned_data["grid_template_area"])

    def test_empty_value_returns_unchanged(self):
        form = DeviceViewForm(data=self._form_data(""))
        # blank=False on the model field -- empty value should fail
        # validation, but clean_grid_template_area itself must not raise.
        form.is_valid()
        self.assertIn("grid_template_area", form.errors)
