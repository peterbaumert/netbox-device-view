"""Request-level tests for the plugin's helper views."""

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    RearPort,
    Site,
)
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from users.models import User

from netbox_device_view.models import DeviceView, RenderMode

INTERFACES_URL = "plugins:netbox_device_view:devicetype_interfaces"
GRID = '.deviceview.area{grid-template-areas:"gi1 port-1";}'


class DeviceTypeInterfacesViewTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user(username="viewer"))

    def test_non_ascii_or_oversized_device_type_id_returns_empty_list(self):
        for value in ("²", "١", "9" * 5000):
            with self.subTest(value=value[:8]):
                response = self.client.get(
                    reverse(INTERFACES_URL), {"device_type_id": value}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), [])

    def test_device_type_id_above_bigint_is_not_queried(self):
        # PostgreSQL compares a bigint id with a larger value as numeric, without the index.
        value = str(2**63)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                reverse(INTERFACES_URL), {"device_type_id": value}
            )
        self.assertEqual(response.json(), [])
        self.assertFalse([q["sql"] for q in queries if value in q["sql"]])


LAYOUT = "version: 1\nviews:\n  front:\n    elements: [{key: gi1}, {key: port-1}]\n"


class DeviceViewTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Switch", slug="switch"
        )
        cls.device = Device.objects.create(
            name="sw1",
            device_type=cls.device_type,
            site=Site.objects.create(name="Site", slug="site"),
            role=DeviceRole.objects.create(name="Role", slug="role"),
        )
        cls.device_view = DeviceView.objects.create(
            device_type=cls.device_type, grid_template_area=GRID, yaml_layout=LAYOUT
        )
        cls.user = User.objects.create_user(username="admin", is_superuser=True)

    def setUp(self):
        self.client.force_login(self.user)

    def _tab(self, render_mode):
        DeviceView.objects.filter(pk=self.device_view.pk).update(
            render_mode=render_mode
        )
        response = self.client.get(
            reverse("dcim:device_deviceview", kwargs={"pk": self.device.pk})
        )
        self.assertEqual(response.status_code, 200)
        return response

    def test_css_mode_renders_one_grid_per_device(self):
        Interface.objects.create(device=self.device, name="Gi1", type="1000base-t")
        response = self._tab(RenderMode.CSS)
        self.assertContains(response, 'class="deviceview area dsw1', count=1)
        self.assertNotContains(response, 'class="dv-svg-wrapper')

    def test_svg_mode_renders_the_panel_of_the_device(self):
        Interface.objects.create(device=self.device, name="Gi1", type="1000base-t")
        response = self._tab(RenderMode.SVG)
        self.assertContains(response, 'id="dv-svg-wrapper-sw1"', count=1)

    def test_svg_mode_renders_all_panels_when_no_port_key_is_the_device_name(self):
        # Patch panel ports have the keys "Front" and "Rear".
        RearPort.objects.create(
            device=self.device, name="Port 1", type="8p8c", positions=1
        )
        response = self._tab(RenderMode.SVG)
        self.assertContains(response, 'id="dv-svg-wrapper-sw1"', count=1)

    def test_svg_mode_without_ports_renders_no_panel(self):
        response = self._tab(RenderMode.SVG)
        self.assertNotContains(response, 'class="dv-svg-wrapper')
