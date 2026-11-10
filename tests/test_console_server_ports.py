"""Device views bind the console-server ports of a device and of its modules by key."""

from dcim.models import (
    Cable,
    ConsolePort,
    ConsoleServerPort,
    ConsoleServerPortTemplate,
    Device,
    DeviceRole,
    DeviceType,
    Manufacturer,
    Module,
    ModuleBay,
    ModuleType,
    Site,
    VirtualChassis,
)
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from users.models import User

from netbox_device_view.models import DeviceView, RenderMode

GRID = '.deviceview.area{grid-template-areas:"ttys1 async0-1-0";}'
STYLENAMES = {"ttyS1": "ttys1", "Async0/1/0": "async0-1-0"}
LAYOUT = (
    "version: 1\nviews:\n  front:\n    elements: [{key: ttys1}, {key: async0-1-0}]\n"
)


class ConsoleServerPortTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        site = Site.objects.create(name="Site", slug="site")
        role = DeviceRole.objects.create(name="Role", slug="role")
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="ACS", slug="acs"
        )
        cls.device = Device.objects.create(
            name="cs1", device_type=cls.device_type, site=site, role=role
        )
        cls.device_view = DeviceView.objects.create(
            device_type=cls.device_type, grid_template_area=GRID, yaml_layout=LAYOUT
        )
        module_type = ModuleType.objects.create(
            manufacturer=manufacturer, model="NIM-16A"
        )
        ConsoleServerPortTemplate.objects.create(
            module_type=module_type, name="Async0/{module}/0", type="rj-45"
        )
        module = Module.objects.create(
            device=cls.device,
            module_bay=ModuleBay.objects.create(
                device=cls.device, name="NIM 1", position="1"
            ),
            module_type=module_type,
        )
        peer_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Server", slug="server"
        )
        device_port = ConsoleServerPort.objects.create(
            device=cls.device, name="ttyS1", type="rj-45"
        )
        # The module creates its port from the template, with the bay position.
        module_port = ConsoleServerPort.objects.get(module=module)
        cls.ports = {}
        for port, peer_name in ((device_port, "srv1"), (module_port, "srv2")):
            peer = Device.objects.create(
                name=peer_name, device_type=peer_type, site=site, role=role
            )
            Cable(
                a_terminations=[port],
                b_terminations=[
                    ConsolePort.objects.create(device=peer, name="Console")
                ],
            ).save()
            cls.ports[port.name] = (port, f"{peer_name} | Console")
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

    def test_css_view_binds_device_and_module_ports(self):
        response = self._tab(RenderMode.CSS)
        for name, (port, peer) in self.ports.items():
            with self.subTest(name=name):
                self.assertContains(response, f'href="{port.get_absolute_url()}"')
                self.assertContains(response, f"grid-area: {STYLENAMES[name]}")
                self.assertContains(response, f"{name}<hr>{peer}")

    def test_svg_view_binds_device_and_module_ports(self):
        response = self._tab(RenderMode.SVG)
        for name, (port, peer) in self.ports.items():
            with self.subTest(name=name):
                self.assertContains(response, f'data-stylename="{STYLENAMES[name]}"')
                self.assertContains(response, f'detailUrl: "{port.get_absolute_url()}"')
                self.assertContains(response, f'peerLabels: ["{peer}"]')

    def test_rendered_layout_api_reports_device_and_module_ports(self):
        api = APIClient()
        api.force_authenticate(self.user)
        response = api.get(
            reverse(
                "plugins-api:netbox_device_view-api:device_rendered_layout",
                kwargs={"pk": self.device.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        hotspots = {h["name"]: h for h in response.json()["panels"][0]["hotspots"]}
        self.assertEqual(set(hotspots), set(self.ports))
        for name, (port, peer) in self.ports.items():
            with self.subTest(name=name):
                hotspot = hotspots[name]
                self.assertEqual(hotspot["object_type"], "dcim.consoleserverport")
                self.assertEqual(hotspot["object_id"], port.pk)
                self.assertEqual(hotspot["connection_state"], "connected")
                self.assertEqual(hotspot["peer_labels"], [peer])
                self.assertTrue(hotspot["trace_supported"])
                trace = api.get(
                    reverse("dcim-api:consoleserverport-trace", kwargs={"pk": port.pk})
                )
                self.assertEqual(trace.status_code, 200)


class VirtualChassisTests(TestCase):
    def test_tab_binds_the_console_server_ports_of_every_member(self):
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        site = Site.objects.create(name="Site", slug="site")
        role = DeviceRole.objects.create(name="Role", slug="role")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="ACS", slug="acs"
        )
        device_view = DeviceView.objects.create(
            device_type=device_type, grid_template_area=GRID, yaml_layout=LAYOUT
        )
        virtual_chassis = VirtualChassis.objects.create(name="vc")
        ports = []
        for position in (1, 2):
            member = Device.objects.create(
                name=f"cs{position}",
                device_type=device_type,
                site=site,
                role=role,
                virtual_chassis=virtual_chassis,
                vc_position=position,
            )
            ports.append(
                ConsoleServerPort.objects.create(
                    device=member, name="ttyS1", type="rj-45"
                )
            )
        self.client.force_login(
            User.objects.create_user(username="admin", is_superuser=True)
        )
        for mode in RenderMode.values:
            DeviceView.objects.filter(pk=device_view.pk).update(render_mode=mode)
            response = self.client.get(
                reverse("dcim:device_deviceview", kwargs={"pk": ports[0].device.pk})
            )
            for port in ports:
                with self.subTest(mode=mode, device=port.device.name):
                    self.assertContains(response, port.get_absolute_url())


class DeviceTypePreviewTests(TestCase):
    def test_form_preview_lists_console_server_port_templates(self):
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="ACS", slug="acs"
        )
        ConsoleServerPortTemplate.objects.create(
            device_type=device_type, name="ttyS1", type="rj-45"
        )
        self.client.force_login(User.objects.create_user(username="viewer"))
        response = self.client.get(
            reverse("plugins:netbox_device_view:devicetype_interfaces"),
            {"device_type_id": device_type.pk},
        )
        self.assertEqual(
            response.json(),
            [
                {
                    "name": "ttyS1",
                    "stylename": "ttys1",
                    "kind": "Console Server Port",
                    "type": "RJ-45",
                }
            ],
        )
