"""A device view stores only a YAML layout that parses; the device page reports one that does not."""

import copy

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from users.models import User

from netbox_device_view.models import DeviceView, RenderMode

GRID = '.deviceview.area{grid-template-areas:"a";}'
VALID_YAML = "version: 1\nviews:\n  front:\n    elements: [{key: a}]\n"
INVALID_YAML = (
    "version: 1\nviews:\n  front:\n    elements:\n      - {key: a, label: 5}\n"
)
INVALID_ERROR = "views.front.elements[0].label: must be a string."


class SaveValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Switch", slug="switch"
        )
        cls.user = User.objects.create_user(username="admin", is_superuser=True)

    def setUp(self):
        self.client.force_login(self.user)

    def _add(self, yaml_layout):
        return self.client.post(
            reverse("plugins:netbox_device_view:deviceview_add"),
            {
                "device_type": self.device_type.pk,
                "grid_template_area": GRID,
                "yaml_layout": yaml_layout,
                "render_mode": "css",
            },
        )

    def test_add_form_rejects_a_layout_that_does_not_parse(self):
        response = self._add(INVALID_YAML)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].errors["yaml_layout"], [INVALID_ERROR]
        )
        self.assertFalse(DeviceView.objects.exists())

    def test_add_form_accepts_a_valid_or_empty_layout(self):
        for yaml_layout in (VALID_YAML, ""):
            with self.subTest(yaml_layout=yaml_layout):
                response = self._add(yaml_layout)
                self.assertEqual(response.status_code, 302)
                DeviceView.objects.all().delete()

    def test_bulk_import_rejects_a_layout_that_does_not_parse(self):
        row = f"""- device_type: {self.device_type.model}
  grid_template_area: '{GRID}'
  yaml_layout: |
{"".join(f"    {line}" + chr(10) for line in INVALID_YAML.splitlines())}"""
        response = self.client.post(
            reverse("plugins:netbox_device_view:deviceview_import"),
            {"import_method": "direct", "data": row, "format": "yaml"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"yaml_layout: {INVALID_ERROR}")
        self.assertFalse(DeviceView.objects.exists())

    def test_api_rejects_a_layout_that_does_not_parse_on_create_and_update(self):
        api = APIClient()
        api.force_authenticate(self.user)
        url = reverse("plugins-api:netbox_device_view-api:deviceview-list")
        response = api.post(
            url,
            {
                "device_type": self.device_type.pk,
                "grid_template_area": GRID,
                "yaml_layout": INVALID_YAML,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"yaml_layout": [INVALID_ERROR]})
        self.assertFalse(DeviceView.objects.exists())

        device_view = DeviceView.objects.create(
            device_type=self.device_type, grid_template_area=GRID
        )
        response = api.patch(
            reverse(
                "plugins-api:netbox_device_view-api:deviceview-detail",
                kwargs={"pk": device_view.pk},
            ),
            {"yaml_layout": INVALID_YAML},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"yaml_layout": [INVALID_ERROR]})
        device_view.refresh_from_db()
        self.assertEqual(device_view.yaml_layout, "")


def _plugin_config(**options):
    config = copy.deepcopy(settings.PLUGINS_CONFIG)
    config["netbox_device_view"].update(options)
    return config


class StoredInvalidLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Switch", slug="switch"
        )
        cls.device = Device.objects.create(
            name="sw1",
            device_type=device_type,
            site=Site.objects.create(name="Site", slug="site"),
            role=DeviceRole.objects.create(name="Role", slug="role"),
        )
        # save() does not call clean(), as for a layout stored before the check.
        cls.device_view = DeviceView.objects.create(
            device_type=device_type, grid_template_area=GRID, yaml_layout=INVALID_YAML
        )
        cls.user = User.objects.create_user(username="admin", is_superuser=True)

    def setUp(self):
        self.client.force_login(self.user)

    def _assert_reports_the_error(self, url):
        for mode in RenderMode.values:
            with self.subTest(mode=mode):
                DeviceView.objects.filter(pk=self.device_view.pk).update(
                    render_mode=mode
                )
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "its YAML layout does not parse")
                self.assertContains(response, INVALID_ERROR)

    def test_device_view_tab_reports_the_parse_error(self):
        self._assert_reports_the_error(
            reverse("dcim:device_deviceview", kwargs={"pk": self.device.pk})
        )

    def test_device_page_panel_reports_the_parse_error(self):
        for position in ("top", "bottom"):
            config = _plugin_config(
                show_on_device_tab=True, device_tab_position=position
            )
            with (
                self.subTest(position=position),
                override_settings(PLUGINS_CONFIG=config),
            ):
                self._assert_reports_the_error(self.device.get_absolute_url())
