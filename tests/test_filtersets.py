"""DeviceViewFilterSet, and the Device Views list page and REST API that apply it."""

from dcim.models import DeviceType, Manufacturer
from django.test import TestCase
from django.urls import reverse
from extras.models import Tag
from rest_framework.test import APIClient
from users.models import User

from netbox_device_view.filtersets import DeviceViewFilterSet
from netbox_device_view.models import DeviceView


class DeviceViewTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cisco = Manufacturer.objects.create(name="Cisco", slug="cisco")
        ubiquiti = Manufacturer.objects.create(name="Ubiquiti", slug="ubiquiti")

        cls.cisco_type = DeviceType.objects.create(
            manufacturer=cisco, model="C9300-24T", slug="c9300-24t"
        )
        cls.ubiquiti_type = DeviceType.objects.create(
            manufacturer=ubiquiti,
            model="USW-Enterprise-24-PoE",
            slug="usw-enterprise-24-poe",
        )

        cls.cisco_view = DeviceView.objects.create(
            device_type=cls.cisco_type, grid_template_area=".area { }"
        )
        cls.ubiquiti_view = DeviceView.objects.create(
            device_type=cls.ubiquiti_type, grid_template_area=".area { }"
        )


class DeviceViewFilterSetSearchTests(DeviceViewTestBase):

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


class DeviceViewListFilterTests(DeviceViewTestBase):
    """The list page and the REST API apply the filter set."""

    LIST_URL = "plugins:netbox_device_view:deviceview_list"
    API_URL = "plugins-api:netbox_device_view-api:deviceview-list"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(username="admin", is_superuser=True)

    def _list(self, params):
        self.client.force_login(self.user)
        response = self.client.get(reverse(self.LIST_URL), params)
        self.assertEqual(response.status_code, 200)
        return response

    def _api(self, params):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get(reverse(self.API_URL), params)
        self.assertEqual(response.status_code, 200)
        return {row["id"] for row in response.json()["results"]}

    def test_list_quick_search_shows_only_matching_device_views(self):
        for query, shown, hidden in (
            ("C9300", self.cisco_type, self.ubiquiti_type),
            ("ubiquiti", self.ubiquiti_type, self.cisco_type),
        ):
            with self.subTest(query):
                response = self._list({"q": query})
                self.assertContains(response, shown.get_absolute_url())
                self.assertNotContains(response, hidden.get_absolute_url())

    def test_list_filters_by_manufacturer_and_device_type(self):
        for params in (
            {"manufacturer_id": self.cisco_type.manufacturer_id},
            {"device_type_id": self.cisco_type.pk},
        ):
            with self.subTest(params):
                response = self._list(params)
                self.assertContains(response, self.cisco_type.get_absolute_url())
                self.assertNotContains(
                    response, self.ubiquiti_type.get_absolute_url()
                )

    def test_list_has_a_filters_tab(self):
        response = self._list({})
        self.assertContains(response, 'id="filters-form"')
        self.assertContains(response, 'name="manufacturer_id"')
        self.assertContains(response, 'name="device_type_id"')

    def test_list_offers_no_bulk_action_without_a_view(self):
        response = self._list({})
        for name in ("_edit", "_rename", "_delete"):
            self.assertNotContains(response, f'name="{name}"')
        # Without a URL, NetBox renders the button with formaction "None".
        self.assertNotContains(response, 'formaction="None')

    def test_api_applies_the_filters(self):
        cisco, ubiquiti = self.cisco_view.pk, self.ubiquiti_view.pk
        self.ubiquiti_view.tags.add(Tag.objects.create(name="Lab", slug="lab"))
        for params, expected in (
            ({"q": "C9300"}, {cisco}),
            ({"q": "ubiquiti"}, {ubiquiti}),
            ({"device_type_id": self.cisco_type.pk}, {cisco}),
            ({"device_type": self.ubiquiti_type.slug}, {ubiquiti}),
            ({"manufacturer_id": self.cisco_type.manufacturer_id}, {cisco}),
            ({"manufacturer": "ubiquiti"}, {ubiquiti}),
            ({"id": cisco}, {cisco}),
            ({"tag": "lab"}, {ubiquiti}),
            ({}, {cisco, ubiquiti}),
        ):
            with self.subTest(params):
                self.assertEqual(self._api(params), expected)
