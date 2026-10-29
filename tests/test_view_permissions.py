"""The rendered-layout API and the device view panels show only objects that the user can view."""

import json

from core.models import ObjectType
from dcim.models import (
    Cable,
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Module,
    ModuleBay,
    ModuleType,
    Site,
    VirtualChassis,
)
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from users.constants import TOKEN_PREFIX
from users.models import ObjectPermission, Token, User

from netbox_device_view.layout.renderers.svg import COLOUR_PORT_DEFAULT
from netbox_device_view.models import DeviceView, RenderMode

GRID = '.deviceview.area{grid-template-areas:"gi-1 gi-2 te-1";}'
LAYOUT = """
version: 1
views:
  front:
    elements: [{key: gi-1}, {key: gi-2}]
variants:
  NIM-HIDDEN:
    elements: [{key: gi-1}, {key: gi-2}, {key: te-1}]
"""
# The device page panel and the Device View tab start their card with these.
CARDS = ('<h2 class="card-header">Deviceview', '<div class="card-body htmx-container">')


class ViewPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        site = Site.objects.create(name="Site", slug="site")
        role = DeviceRole.objects.create(name="Role", slug="role")
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Switch", slug="switch"
        )
        cls.device_view = DeviceView.objects.create(
            device_type=cls.device_type, grid_template_area=GRID, yaml_layout=LAYOUT
        )
        chassis = VirtualChassis.objects.create(name="vc1")
        cls.sw1, cls.sw2 = (
            Device.objects.create(
                name=name,
                device_type=cls.device_type,
                site=site,
                role=role,
                virtual_chassis=chassis,
                vc_position=position,
            )
            for position, name in ((1, "sw1"), (2, "sw2"))
        )
        peer = Device.objects.create(
            name="peer1",
            device_type=DeviceType.objects.create(
                manufacturer=manufacturer, model="Server", slug="server"
            ),
            site=site,
            role=role,
        )
        for name in ("Gi1", "Gi2", "Te1/1"):
            Interface.objects.create(device=cls.sw1, name=name, type="1000base-t")
        Interface.objects.create(device=cls.sw2, name="Gi1", type="1000base-t")
        for name, color in (("Gi1", "ff0000"), ("Gi2", "00ff00")):
            Cable(
                a_terminations=[Interface.objects.get(device=cls.sw1, name=name)],
                b_terminations=[
                    Interface.objects.create(
                        device=peer, name=f"eth-{name}", type="1000base-t"
                    )
                ],
                color=color,
            ).save()
        Module.objects.create(
            device=cls.sw1,
            module_bay=ModuleBay.objects.create(device=cls.sw1, name="NIM 1"),
            module_type=ModuleType.objects.create(
                manufacturer=manufacturer, model="NIM-HIDDEN"
            ),
        )
        cls.user = User.objects.create_user(username="viewer")
        cls._grant(cls.user, "dcim.view_device", {"name": "sw1"})
        cls._grant(
            cls.user,
            "dcim.view_interface",
            {"device__name": "sw1", "name__in": ["Gi1", "Te1/1"]},
        )
        cls._grant(cls.user, "netbox_device_view.view_deviceview")

    @staticmethod
    def _grant(user, name, constraints=None):
        app_label, action_model = name.split(".")
        action, model = action_model.split("_", 1)
        permission = ObjectPermission.objects.create(
            name=f"{name} {constraints}", actions=[action], constraints=constraints
        )
        permission.users.add(user)
        permission.object_types.add(
            ObjectType.objects.get_by_natural_key(app_label, model)
        )

    def _api(self, device=None):
        token = Token.objects.create(user=self.user)
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {TOKEN_PREFIX}{token.key}.{token.token}"
        )
        response = client.get(
            reverse(
                "plugins-api:netbox_device_view-api:device_rendered_layout",
                kwargs={"pk": (device or self.sw1).pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _panel(self, render_mode, device=None, status=200, cable_colors="off"):
        DeviceView.objects.filter(pk=self.device_view.pk).update(
            render_mode=render_mode
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("dcim:device_deviceview", kwargs={"pk": (device or self.sw1).pk}),
            {"cable_colors": cable_colors},
        )
        self.assertEqual(response.status_code, status)
        return self._card(response.content.decode())

    def _card(self, content):
        for card in CARDS:
            if card in content:
                return content[content.find(card) :]
        return ""

    def _hotspots(self, body):
        return {hotspot["name"]: hotspot for hotspot in body["panels"][0]["hotspots"]}

    # The rendered-layout API

    def test_api_leaves_out_a_member_that_the_user_cannot_view(self):
        body = self._api()
        self.assertEqual([panel["key"] for panel in body["panels"]], ["front"])
        self.assertEqual(body["panels"][0]["label"], "Front")
        self.assertNotIn("sw2", json.dumps(body))

    def test_api_gives_no_hotspot_and_no_status_fill_for_a_hidden_component(self):
        # With cable view, a leaked status of Gi2 shows as the colour of its cable.
        self._grant(self.user, "dcim.view_cable")
        body = self._api()
        self.assertNotIn("Gi2", self._hotspots(body))
        self.assertNotIn("#00ff00", body["panels"][0]["svg"])
        self.assertIn(f'fill="{COLOUR_PORT_DEFAULT}"', body["panels"][0]["svg"])

    def test_api_leaves_out_a_peer_that_the_user_cannot_view(self):
        self.assertEqual(self._hotspots(self._api())["Gi1"]["peer_labels"], [])
        self._grant(self.user, "dcim.view_interface", {"device__name": "peer1"})
        self.assertEqual(
            self._hotspots(self._api())["Gi1"]["peer_labels"], ["peer1 | eth-Gi1"]
        )

    def test_api_gives_no_colour_of_a_cable_that_the_user_cannot_view(self):
        body = self._api()
        self.assertIsNone(self._hotspots(body)["Gi1"]["cable_color"])
        self.assertNotIn("#ff0000", body["panels"][0]["svg"])
        self._grant(self.user, "dcim.view_cable")
        body = self._api()
        self.assertEqual(self._hotspots(body)["Gi1"]["cable_color"], "ff0000")
        self.assertIn("#ff0000", body["panels"][0]["svg"])

    def test_api_ignores_a_module_that_the_user_cannot_view(self):
        self.assertNotIn("Te1/1", self._hotspots(self._api()))
        self._grant(self.user, "dcim.view_module")
        self.assertIn("Te1/1", self._hotspots(self._api()))

    def test_api_reports_no_layout_for_a_device_view_that_the_user_cannot_view(self):
        ObjectPermission.objects.filter(name__startswith="netbox_device_view").update(
            constraints={"device_type__model": "Other"}
        )
        body = self._api()
        self.assertFalse(body["available"])
        self.assertEqual(body["reason"], "no_layout")

    # The Device View tab

    def test_tab_of_a_device_that_the_user_cannot_view_is_not_found(self):
        self._panel(RenderMode.SVG, device=self.sw2, status=404)

    def test_tab_leaves_out_a_member_that_the_user_cannot_view(self):
        for render_mode in (RenderMode.SVG, RenderMode.CSS):
            with self.subTest(render_mode):
                panel = self._panel(render_mode)
                self.assertIn("Gi1", panel)
                self.assertNotIn("sw2", panel)
                self.assertNotIn('data-device="2"', panel)
                self.assertNotIn("area d2", panel)

    def test_tab_leaves_out_a_component_that_the_user_cannot_view(self):
        self._grant(self.user, "dcim.view_cable")
        for render_mode in (RenderMode.SVG, RenderMode.CSS):
            with self.subTest(render_mode):
                panel = self._panel(render_mode, cable_colors="on")
                self.assertIn("Gi1", panel)
                self.assertNotIn("Gi2", panel)
                self.assertNotIn("00ff00", panel)

    def test_tab_leaves_out_a_peer_that_the_user_cannot_view(self):
        for render_mode in (RenderMode.SVG, RenderMode.CSS):
            with self.subTest(render_mode):
                self.assertNotIn("peer1", self._panel(render_mode))
        self._grant(self.user, "dcim.view_interface", {"device__name": "peer1"})
        for render_mode in (RenderMode.SVG, RenderMode.CSS):
            with self.subTest(render_mode):
                self.assertIn("peer1 | eth-Gi1", self._panel(render_mode))

    def test_tab_gives_no_colour_of_a_cable_that_the_user_cannot_view(self):
        for render_mode in (RenderMode.SVG, RenderMode.CSS):
            with self.subTest(render_mode):
                panel = self._panel(render_mode, cable_colors="on")
                self.assertIn("Gi1", panel)
                self.assertNotIn("ff0000", panel)
        self._grant(self.user, "dcim.view_cable")
        for render_mode in (RenderMode.SVG, RenderMode.CSS):
            with self.subTest(render_mode):
                self.assertIn("ff0000", self._panel(render_mode, cable_colors="on"))

    def test_tab_is_hidden_for_a_device_view_that_the_user_cannot_view(self):
        tab_url = reverse("dcim:device_deviceview", kwargs={"pk": self.sw1.pk})
        self.client.force_login(self.user)
        self.assertContains(self.client.get(self.sw1.get_absolute_url()), tab_url)
        ObjectPermission.objects.filter(name__startswith="netbox_device_view").update(
            constraints={"device_type__model": "Other"}
        )
        self.assertNotContains(self.client.get(self.sw1.get_absolute_url()), tab_url)

    def test_tab_ignores_a_module_that_the_user_cannot_view(self):
        self.assertNotIn("moduleNIM-HIDDEN", self._panel(RenderMode.CSS))
        self.assertNotIn("te-1", self._panel(RenderMode.SVG).split("<script>")[0])
        self._grant(self.user, "dcim.view_module")
        self.assertIn("moduleNIM-HIDDEN", self._panel(RenderMode.CSS))
        self.assertIn("te-1", self._panel(RenderMode.SVG).split("<script>")[0])

    def test_tab_shows_no_panel_for_a_device_view_that_the_user_cannot_view(self):
        ObjectPermission.objects.filter(name__startswith="netbox_device_view").update(
            constraints={"device_type__model": "Other"}
        )
        for render_mode in (RenderMode.SVG, RenderMode.CSS):
            with self.subTest(render_mode):
                panel = self._panel(render_mode)
                self.assertNotIn("Gi1", panel)
                self.assertNotIn("dv-svg-wrapper", panel)

    def _device_page_panel(self):
        plugins_config = {
            **settings.PLUGINS_CONFIG,
            "netbox_device_view": {
                **settings.PLUGINS_CONFIG["netbox_device_view"],
                "show_on_device_tab": True,
            },
        }
        self.client.force_login(self.user)
        with override_settings(PLUGINS_CONFIG=plugins_config):
            return self._card(
                self.client.get(self.sw1.get_absolute_url()).content.decode()
            )

    def test_device_page_panel_leaves_out_hidden_objects(self):
        panel = self._device_page_panel()
        self.assertIn("Gi1", panel)
        for hidden in ("Gi2", "sw2", "peer1"):
            self.assertNotIn(hidden, panel)

    def test_device_page_panel_names_a_virtual_chassis_only_when_the_user_can_view_it(self):
        self.assertIn("Deviceview sw1<", self._device_page_panel())
        self._grant(self.user, "dcim.view_virtualchassis")
        self.assertIn("Deviceview vc1<", self._device_page_panel())
