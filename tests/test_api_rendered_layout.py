"""
Tests for the device rendered-layout API endpoint
(GET /api/plugins/device_view/devices/<pk>/rendered-layout/).

Uses real DB-backed fixtures (unlike test_utils.py's mock-based unit tests)
since this endpoint's behaviour is defined by NetBox's own permission
system, cable/interface relationships, and Django URL routing — none of
which can be exercised with SimpleNamespace mocks.
"""

from core.models import ObjectType
from dcim.models import (
    Cable,
    ConsolePort,
    Device,
    DeviceRole,
    DeviceType,
    FrontPort,
    Interface,
    Manufacturer,
    RearPort,
    Site,
    VirtualChassis,
)
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from users.constants import TOKEN_PREFIX
from users.models import ObjectPermission, Token, User

from netbox_device_view.models import DeviceView, RenderMode

SVG_YAML = """
version: 1
canvas:
  columns: 8
  rows: 1
  cell_size: 24
views:
  front:
    rows:
      - sequence:
          kind: interface
          prefix: "gigabitethernet-"
          start: 1
          count: 4
          pattern: all
"""

PATCH_PANEL_YAML = """
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 24
views:
  front:
    rows:
      - sequence:
          kind: port
          prefix: "port-"
          start: 1
          count: 2
          pattern: all
  rear:
    rows:
      - sequence:
          kind: port
          prefix: "port-"
          start: 1
          count: 2
          pattern: all
"""

INVALID_YAML = "version: 1\nviews: [this is not closed"


class RenderedLayoutTestBase(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        self.device_type = DeviceType.objects.create(
            manufacturer=self.manufacturer,
            model="Switch4",
            slug="switch4",
            u_height=1,
        )
        self.role = DeviceRole.objects.create(name="Role", slug="role")
        self.site = Site.objects.create(name="Site", slug="site")
        self.device = Device.objects.create(
            name="dev1",
            device_type=self.device_type,
            role=self.role,
            site=self.site,
            status="active",
        )
        self.user = User.objects.create_user(username="tester")
        self.client = APIClient()

    def _grant(self, name, constraints=None):
        app_label, action_model = name.split(".")
        action, model = action_model.split("_", 1)
        object_type = ObjectType.objects.get_by_natural_key(app_label, model)
        perm = ObjectPermission(name=name, actions=[action], constraints=constraints)
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(object_type)

    def _authenticate(self):
        # NetBox 4.6+ tokens default to v2 (peppered/digest) format, where
        # `.key` alone is only a short public identifier, not the bearer
        # secret — `Authorization: Token <key>` (v1 scheme) silently fails
        # auth for a v2 token. Use the real v2 Bearer scheme, matching
        # NetBox's own utilities.testing.api.APITestCase.setUp().
        token = Token.objects.create(user=self.user)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {TOKEN_PREFIX}{token.key}.{token.token}"
        )

    def _url(self, device=None):
        return reverse(
            "plugins-api:netbox_device_view-api:device_rendered_layout",
            kwargs={"pk": (device or self.device).pk},
        )

    def _make_interfaces(self, count=4, device=None):
        device = device or self.device
        return [
            Interface.objects.create(
                device=device,
                name=f"GigabitEthernet0/{n}",
                type="1000base-t",
                enabled=True,
            )
            for n in range(1, count + 1)
        ]


class AuthenticationTests(RenderedLayoutTestBase):
    def test_anonymous_request_is_rejected(self):
        response = self.client.get(self._url())
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_authenticated_without_permission_is_403(self):
        self._authenticate()
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_authenticated_with_permission_succeeds(self):
        self._authenticate()
        self._grant("dcim.view_device")
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ObjectPermissionTests(RenderedLayoutTestBase):
    def test_device_outside_permission_scope_is_404_not_403(self):
        other_site = Site.objects.create(name="Other Site", slug="other-site")
        other_device = Device.objects.create(
            name="dev2",
            device_type=self.device_type,
            role=self.role,
            site=other_site,
            status="active",
        )
        self._authenticate()
        self._grant("dcim.view_device", constraints={"site__slug": self.site.slug})

        in_scope = self.client.get(self._url(self.device))
        self.assertEqual(in_scope.status_code, status.HTTP_200_OK)

        out_of_scope = self.client.get(self._url(other_device))
        self.assertEqual(out_of_scope.status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_device_is_404(self):
        self._authenticate()
        self._grant("dcim.view_device")
        response = self.client.get(self._url(device=type("D", (), {"pk": 999999})()))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AvailabilityTests(RenderedLayoutTestBase):
    def setUp(self):
        super().setUp()
        self._authenticate()
        self._grant("dcim.view_device")

    def test_no_device_view_configured(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertFalse(data["available"])
        self.assertEqual(data["reason"], "no_layout")
        self.assertEqual(data["panels"], [])
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["device_id"], self.device.pk)

    def test_css_only_layout_requires_svg(self):
        DeviceView.objects.create(
            device_type=self.device_type,
            grid_template_area="some legacy css",
            yaml_layout="",
            render_mode=RenderMode.CSS,
        )
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertFalse(data["available"])
        self.assertEqual(data["reason"], "svg_layout_required")

    def test_yaml_layout_renders_regardless_of_web_render_mode_preference(self):
        # The mobile API always wants SVG; a YAML layout with render_mode
        # left at the CSS default (a plausible admin default/oversight)
        # should still produce SVG here — only has_yaml_layout gates this.
        DeviceView.objects.create(
            device_type=self.device_type,
            grid_template_area="",
            yaml_layout=SVG_YAML,
            render_mode=RenderMode.CSS,
        )
        self._make_interfaces()
        response = self.client.get(self._url())
        data = response.json()
        self.assertTrue(data["available"])

    def test_invalid_yaml_reports_safe_reason_not_traceback(self):
        DeviceView.objects.create(
            device_type=self.device_type,
            grid_template_area="",
            yaml_layout=INVALID_YAML,
            render_mode=RenderMode.SVG,
        )
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertFalse(data["available"])
        self.assertEqual(data["reason"], "invalid_layout")
        body_text = response.content.decode()
        self.assertNotIn("Traceback", body_text)
        self.assertNotIn(".py", body_text)


class RenderedPanelTests(RenderedLayoutTestBase):
    def setUp(self):
        super().setUp()
        self._authenticate()
        self._grant("dcim.view_device")
        self.device_view = DeviceView.objects.create(
            device_type=self.device_type,
            grid_template_area="",
            yaml_layout=SVG_YAML,
            render_mode=RenderMode.SVG,
        )

    def test_schema_shape(self):
        self._make_interfaces()
        response = self.client.get(self._url())
        data = response.json()
        self.assertEqual(
            set(data.keys()),
            {
                "schema_version",
                "available",
                "reason",
                "device_id",
                "render_mode",
                "panels",
            },
        )
        self.assertEqual(len(data["panels"]), 1)
        panel = data["panels"][0]
        self.assertEqual(
            set(panel.keys()), {"key", "label", "width", "height", "svg", "hotspots"}
        )
        self.assertGreater(panel["width"], 0)
        self.assertGreater(panel["height"], 0)
        self.assertIn("<svg", panel["svg"])

    def test_hotspot_shape_and_no_svg_text_elements(self):
        self._make_interfaces()
        response = self.client.get(self._url())
        panel = response.json()["panels"][0]
        self.assertEqual(len(panel["hotspots"]), 4)
        hotspot = panel["hotspots"][0]
        self.assertEqual(
            set(hotspot.keys()),
            {
                "object_type",
                "object_id",
                "name",
                "x",
                "y",
                "width",
                "height",
                "enabled",
                "connection_state",
                "cable_color",
                "peer_labels",
                "trace_supported",
            },
        )
        # flutter_svg can't reliably centre <text> — labels travel as
        # hotspot.name instead; the SVG itself must carry none.
        self.assertNotIn("<text", panel["svg"])
        self.assertNotIn("class=", panel["svg"])
        self.assertNotIn("dominant-baseline", panel["svg"])

    def test_disabled_interface_is_disabled_state_with_no_cable(self):
        ifaces = self._make_interfaces()
        ifaces[0].enabled = False
        ifaces[0].save(update_fields=["enabled"])

        response = self.client.get(self._url())
        hotspots = {h["name"]: h for h in response.json()["panels"][0]["hotspots"]}
        h = hotspots["GigabitEthernet0/1"]
        self.assertFalse(h["enabled"])
        self.assertEqual(h["connection_state"], "disabled")
        self.assertIsNone(h["cable_color"])
        self.assertEqual(h["peer_labels"], [])
        self.assertTrue(h["trace_supported"])

    def test_enabled_unconnected_interface(self):
        self._make_interfaces()
        response = self.client.get(self._url())
        hotspots = {h["name"]: h for h in response.json()["panels"][0]["hotspots"]}
        h = hotspots["GigabitEthernet0/1"]
        self.assertTrue(h["enabled"])
        self.assertEqual(h["connection_state"], "enabled")
        self.assertIsNone(h["cable_color"])

    def test_connected_interface_with_cable_color(self):
        ifaces = self._make_interfaces()
        Cable(
            a_terminations=[ifaces[0]], b_terminations=[ifaces[1]], color="ff0000"
        ).save()

        response = self.client.get(self._url())
        hotspots = {h["name"]: h for h in response.json()["panels"][0]["hotspots"]}

        h1 = hotspots["GigabitEthernet0/1"]
        self.assertEqual(h1["connection_state"], "connected")
        self.assertEqual(h1["cable_color"], "ff0000")
        self.assertEqual(h1["peer_labels"], ["dev1 | GigabitEthernet0/2"])

        h2 = hotspots["GigabitEthernet0/2"]
        self.assertEqual(h2["connection_state"], "connected")
        self.assertEqual(h2["peer_labels"], ["dev1 | GigabitEthernet0/1"])

    def test_connected_interface_with_no_cable_color_set(self):
        ifaces = self._make_interfaces()
        Cable(a_terminations=[ifaces[0]], b_terminations=[ifaces[1]], color="").save()

        response = self.client.get(self._url())
        hotspots = {h["name"]: h for h in response.json()["panels"][0]["hotspots"]}
        h1 = hotspots["GigabitEthernet0/1"]
        self.assertEqual(h1["cable_color"], "")
        self.assertEqual(h1["connection_state"], "connected")

    def test_console_port_hotspot_has_no_trace_support_false_for_front_rear(self):
        # Front/rear ports use PassThroughPortMixin (no /trace/ action);
        # console ports and interfaces use PathEndpointMixin (have one).
        DeviceView.objects.filter(pk=self.device_view.pk).update(yaml_layout="""
version: 1
canvas:
  columns: 4
  rows: 1
  cell_size: 24
views:
  front:
    rows:
      - sequence:
          kind: console-port
          prefix: "console-"
          start: 1
          count: 1
          pattern: all
""")
        ConsolePort.objects.create(device=self.device, name="Console 1", type="rj-45")
        response = self.client.get(self._url())
        hotspots = response.json()["panels"][0]["hotspots"]
        self.assertEqual(len(hotspots), 1)
        self.assertEqual(hotspots[0]["object_type"], "dcim.consoleport")
        self.assertTrue(hotspots[0]["trace_supported"])


class PatchPanelMultiPanelTests(RenderedLayoutTestBase):
    def setUp(self):
        super().setUp()
        self._authenticate()
        self._grant("dcim.view_device")
        DeviceView.objects.create(
            device_type=self.device_type,
            grid_template_area="",
            yaml_layout=PATCH_PANEL_YAML,
            render_mode=RenderMode.SVG,
        )
        # The composition path only reads .name/.link_peers/.cable per port
        # (see rendered_layout._component_querysets) — it doesn't need a
        # real FrontPort<->RearPort mapping, so these are independent.
        RearPort.objects.create(device=self.device, name="Port 1", type="8p8c")
        RearPort.objects.create(device=self.device, name="Port 2", type="8p8c")
        FrontPort.objects.create(device=self.device, name="Port 1", type="8p8c")
        FrontPort.objects.create(device=self.device, name="Port 2", type="8p8c")

    def test_front_and_rear_faces_return_distinct_panels(self):
        response = self.client.get(self._url())
        data = response.json()
        self.assertTrue(data["available"])
        keys = {p["key"] for p in data["panels"]}
        self.assertEqual(keys, {"front", "rear"})
        for panel in data["panels"]:
            self.assertTrue(
                all(
                    h["object_type"] in ("dcim.frontport", "dcim.rearport")
                    for h in panel["hotspots"]
                )
            )
            self.assertTrue(
                all(h["trace_supported"] is False for h in panel["hotspots"])
            )


class VirtualChassisTests(RenderedLayoutTestBase):
    def setUp(self):
        super().setUp()
        self._authenticate()
        self._grant("dcim.view_device")
        DeviceView.objects.create(
            device_type=self.device_type,
            grid_template_area="",
            yaml_layout=SVG_YAML,
            render_mode=RenderMode.SVG,
        )

    def test_virtual_chassis_produces_one_panel_set_per_member(self):
        vc = VirtualChassis.objects.create(name="VC1")
        self.device.virtual_chassis = vc
        self.device.vc_position = 1
        self.device.save()

        member2 = Device.objects.create(
            name="dev2",
            device_type=self.device_type,
            role=self.role,
            site=self.site,
            status="active",
            virtual_chassis=vc,
            vc_position=2,
        )
        self._make_interfaces(device=self.device)
        self._make_interfaces(device=member2)

        response = self.client.get(self._url())
        data = response.json()
        self.assertTrue(data["available"])
        self.assertEqual(len(data["panels"]), 2)
        keys = {p["key"] for p in data["panels"]}
        self.assertTrue(all(k.startswith("member-") for k in keys))

    def test_any_member_missing_device_view_fails_whole_response(self):
        other_type = DeviceType.objects.create(
            manufacturer=self.manufacturer,
            model="Switch-NoView",
            slug="switch-no-view",
            u_height=1,
        )
        vc = VirtualChassis.objects.create(name="VC2")
        self.device.virtual_chassis = vc
        self.device.vc_position = 1
        self.device.save()
        Device.objects.create(
            name="dev3",
            device_type=other_type,
            role=self.role,
            site=self.site,
            status="active",
            virtual_chassis=vc,
            vc_position=2,
        )

        response = self.client.get(self._url())
        data = response.json()
        self.assertFalse(data["available"])
        self.assertEqual(data["reason"], "no_layout")


class QueryCountTests(RenderedLayoutTestBase):
    """Guards against N+1 regressions in the composition path."""

    def setUp(self):
        super().setUp()
        self._authenticate()
        self._grant("dcim.view_device")
        DeviceView.objects.create(
            device_type=self.device_type,
            grid_template_area="",
            yaml_layout=SVG_YAML,
            render_mode=RenderMode.SVG,
        )

    def test_query_count_does_not_scale_with_interface_count(self):
        ifaces_small = self._make_interfaces(count=4)
        for i in range(0, 4, 2):
            Cable(
                a_terminations=[ifaces_small[i]],
                b_terminations=[ifaces_small[i + 1]],
                color="ff0000",
            ).save()

        with CaptureQueriesContext(connection) as small_ctx:
            self.client.get(self._url())
        small_count = len(small_ctx.captured_queries)

        big_device = Device.objects.create(
            name="dev-big",
            device_type=DeviceType.objects.create(
                manufacturer=self.manufacturer,
                model="Switch40",
                slug="switch40",
                u_height=1,
            ),
            role=self.role,
            site=self.site,
            status="active",
        )
        DeviceView.objects.create(
            device_type=big_device.device_type,
            grid_template_area="",
            yaml_layout=SVG_YAML.replace("count: 4", "count: 40"),
            render_mode=RenderMode.SVG,
        )
        ifaces_big = self._make_interfaces(count=40, device=big_device)
        for i in range(0, 40, 2):
            Cable(
                a_terminations=[ifaces_big[i]],
                b_terminations=[ifaces_big[i + 1]],
                color="ff0000",
            ).save()

        with CaptureQueriesContext(connection) as big_ctx:
            self.client.get(self._url(big_device))
        big_count = len(big_ctx.captured_queries)

        # A handful of extra queries (pagination-free single object, cable
        # trace prefetches) is fine; linear growth with interface count is
        # the N+1 regression this test exists to catch.
        self.assertLess(big_count, small_count + 10)
