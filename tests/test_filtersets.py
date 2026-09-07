"""
Tests for DeviceViewFilterSet, in particular the Quick Search (search())
override that lets the Device Views list view's search box filter by
device type model and manufacturer name.

Uses real DB-backed fixtures (like test_api_rendered_layout.py) since
django-filter's FilterSet needs a real queryset to operate on.
"""

from dcim.models import DeviceType, Manufacturer
from django.test import TestCase

from netbox_device_view.filtersets import DeviceViewFilterSet
from netbox_device_view.models import DeviceView


class DeviceViewFilterSetSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cisco = Manufacturer.objects.create(name="Cisco", slug="cisco")
        ubiquiti = Manufacturer.objects.create(name="Ubiquiti", slug="ubiquiti")

        cls.cisco_type = DeviceType.objects.create(
            manufacturer=cisco, model="C9300-24T"
        )
        cls.ubiquiti_type = DeviceType.objects.create(
            manufacturer=ubiquiti, model="USW-Enterprise-24-PoE"
        )

        cls.cisco_view = DeviceView.objects.create(
            device_type=cls.cisco_type, grid_template_area=".area { }"
        )
        cls.ubiquiti_view = DeviceView.objects.create(
            device_type=cls.ubiquiti_type, grid_template_area=".area { }"
        )

    def test_search_matches_device_type_model(self):
        results = DeviceViewFilterSet({"q": "C9300"}, DeviceView.objects.all()).qs
        self.assertIn(self.cisco_view, results)
        self.assertNotIn(self.ubiquiti_view, results)

    def test_search_matches_manufacturer_name(self):
        results = DeviceViewFilterSet({"q": "Ubiquiti"}, DeviceView.objects.all()).qs
        self.assertIn(self.ubiquiti_view, results)
        self.assertNotIn(self.cisco_view, results)

    def test_search_is_case_insensitive(self):
        results = DeviceViewFilterSet({"q": "c9300"}, DeviceView.objects.all()).qs
        self.assertIn(self.cisco_view, results)

    def test_search_empty_value_returns_all(self):
        results = DeviceViewFilterSet({"q": ""}, DeviceView.objects.all()).qs
        self.assertIn(self.cisco_view, results)
        self.assertIn(self.ubiquiti_view, results)

    def test_search_whitespace_only_returns_all(self):
        results = DeviceViewFilterSet({"q": "   "}, DeviceView.objects.all()).qs
        self.assertIn(self.cisco_view, results)
        self.assertIn(self.ubiquiti_view, results)

    def test_search_no_match_returns_empty(self):
        results = DeviceViewFilterSet(
            {"q": "nonexistent-model-xyz"}, DeviceView.objects.all()
        ).qs
        self.assertEqual(results.count(), 0)
