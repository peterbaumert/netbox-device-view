"""
Tests for netbox_device_view.utils

process_interfaces() and process_ports() derive a `stylename` attribute on each
object and bucket them by device key in ports_chassis.  prepare() orchestrates
those calls around the DB lookup, with real objects.
"""

import types
from unittest.mock import MagicMock

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
    VirtualChassis,
)
from django.test import TestCase
from users.models import User

from netbox_device_view.models import DeviceView
from netbox_device_view.utils import prepare, process_interfaces, process_ports

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_iface(name, iface_type="1000base-t"):
    """Return a minimal interface-like object."""
    obj = types.SimpleNamespace(name=name, type=iface_type)
    return obj


def make_port(name, port_type="8p8c"):
    """Return a minimal port-like object."""
    obj = types.SimpleNamespace(name=name, type=port_type)
    return obj


# ---------------------------------------------------------------------------
# process_interfaces — stylename derivation
# ---------------------------------------------------------------------------


class TestProcessInterfacesStylename:
    """Verify that stylename is set correctly for various interface name formats."""

    def _run(self, name, iface_type="1000base-t"):
        iface = make_iface(name, iface_type)
        result = process_interfaces([iface], {}, "dev1")
        return result["dev1"][0].stylename

    # --- 3-part Cisco format: type + dev (ignored) + module + port -----------

    def test_gigabitethernet_3part(self):
        # GigabitEthernet0/0/1 → gigabitethernet0-1
        assert self._run("GigabitEthernet0/0/1") == "gigabitethernet0-1"

    def test_gigabitethernet_3part_high_port(self):
        # GigabitEthernet0/0/48 → gigabitethernet0-48
        assert self._run("GigabitEthernet0/0/48") == "gigabitethernet0-48"

    def test_tengigabitethernet_3part(self):
        # TenGigabitEthernet1/1/1 → tengigabitethernet1-1
        assert self._run("TenGigabitEthernet1/1/1") == "tengigabitethernet1-1"

    # --- 2-part format: type + dev (consumed) + port, no module --------------

    def test_gigabitethernet_2part(self):
        # GigabitEthernet0/1 → gigabitethernet-1  (module=None → no module in name)
        assert self._run("GigabitEthernet0/1") == "gigabitethernet-1"

    def test_tengigabitethernet_2part(self):
        assert self._run("TenGigabitEthernet0/1") == "tengigabitethernet-1"

    # --- simple type+port without separator ----------------------------------

    def test_eth_simple(self):
        # eth0 → eth-0
        assert self._run("eth0") == "eth-0"

    def test_management(self):
        # Management0 → management-0
        assert self._run("Management0") == "management-0"

    # --- purely numeric name -------------------------------------------------

    def test_numeric_gets_p_prefix(self):
        # "1" → "p1" (isdigit guard)
        assert self._run("1") == "p1"

    def test_numeric_multidigit(self):
        assert self._run("42") == "p42"

    def test_digit_only_name_no_alpha_type(self):
        # "0/0/1" has no alphabetic type prefix → type="" → port-only path → "p1"
        assert self._run("0/0/1") == "p1"

    # --- names with hyphens or underscores in the type -----------------------

    def test_hyphen_in_type_3part(self):
        # "Fast-Ethernet0/0/1" → "fast-ethernet0-1"
        assert self._run("Fast-Ethernet0/0/1") == "fast-ethernet0-1"

    # --- fallback: names that don't end in a digit ---------------------------

    def test_fallback_space_replaced(self):
        # "Front Port" → "front-port" (fallback replaces non-alnum/dot with -)
        assert self._run("Front Port") == "front-port"

    def test_fallback_slash_replaced(self):
        # "eth/uplink" → "eth-uplink"
        assert self._run("eth/uplink") == "eth-uplink"

    # --- virtual and LAG interfaces are skipped ------------------------------

    def test_virtual_skipped(self):
        iface = make_iface("Loopback0", iface_type="virtual")
        result = process_interfaces([iface], {}, "dev1")
        assert "dev1" not in result

    def test_lag_skipped(self):
        iface = make_iface("Port-channel1", iface_type="lag")
        result = process_interfaces([iface], {}, "dev1")
        assert "dev1" not in result

    def test_non_virtual_included(self):
        iface = make_iface("GigabitEthernet0/0/1", iface_type="1000base-t")
        result = process_interfaces([iface], {}, "dev1")
        assert "dev1" in result
        assert len(result["dev1"]) == 1

    # --- ports_chassis accumulation ------------------------------------------

    def test_multiple_interfaces_same_dev(self):
        ifaces = [
            make_iface("GigabitEthernet0/0/1"),
            make_iface("GigabitEthernet0/0/2"),
        ]
        result = process_interfaces(ifaces, {}, "myswitch")
        assert len(result["myswitch"]) == 2

    def test_multiple_devs_independent(self):
        iface1 = make_iface("GigabitEthernet0/0/1")
        iface2 = make_iface("GigabitEthernet0/0/1")
        result = process_interfaces([iface1], {}, "sw1")
        result = process_interfaces([iface2], result, "sw2")
        assert len(result["sw1"]) == 1
        assert len(result["sw2"]) == 1

    def test_existing_ports_chassis_preserved(self):
        sentinel = object()
        existing = {"other_dev": [sentinel]}
        iface = make_iface("GigabitEthernet0/0/1")
        result = process_interfaces([iface], existing, "new_dev")
        assert result["other_dev"] == [sentinel]
        assert len(result["new_dev"]) == 1

    def test_none_interfaces_returns_unchanged(self):
        existing = {"dev": []}
        result = process_interfaces(None, existing, "dev")
        assert result == {"dev": []}


# ---------------------------------------------------------------------------
# process_ports — stylename derivation
# ---------------------------------------------------------------------------


class TestProcessPortsStylename:
    """process_ports uses only the fallback regex path."""

    def _run(self, name, port_type="8p8c"):
        port = make_port(name, port_type)
        result = process_ports([port], {}, "dev1")
        return result["dev1"][0].stylename

    def test_simple_name(self):
        assert self._run("Port1") == "port1"

    def test_space_replaced_with_hyphen(self):
        assert self._run("Front Port 1") == "front-port-1"

    def test_slash_replaced(self):
        assert self._run("Port 0/1") == "port-0-1"

    def test_numeric_gets_p_prefix(self):
        assert self._run("1") == "p1"

    def test_is_port_flag_set(self):
        port = make_port("SomePort")
        process_ports([port], {}, "dev1")
        assert port.is_port is True

    def test_virtual_port_skipped(self):
        port = make_port("VirtualPort", port_type="virtual")
        result = process_ports([port], {}, "dev1")
        assert "dev1" not in result

    def test_none_ports_returns_unchanged(self):
        result = process_ports(None, {}, "dev1")
        assert result == {}


# ---------------------------------------------------------------------------
# prepare() — real device, device view and interfaces
# ---------------------------------------------------------------------------


class TestPrepare(TestCase):
    """prepare() wires the DB lookup + process_* calls together."""

    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Switch", slug="switch"
        )
        cls.site = Site.objects.create(name="Site", slug="site")
        cls.role = DeviceRole.objects.create(name="Role", slug="role")
        cls.user = User.objects.create_user(username="admin", is_superuser=True)

    def _device(self, name="test-switch", **kwargs):
        return Device.objects.create(
            name=name,
            device_type=self.device_type,
            site=self.site,
            role=self.role,
            **kwargs,
        )

    def _device_view(self, css=".area { }"):
        return DeviceView.objects.create(
            device_type=self.device_type, grid_template_area=css
        )

    def test_no_device_view_returns_none_triple(self):
        assert prepare(self._device(), self.user) == (None, None, None, None)

    def test_single_device_returns_dicts(self):
        device_view = self._device_view()

        dv, modules, _ports_chassis, result = prepare(self._device(), self.user)

        assert dv == {1: ".area { }"}
        assert 1 in modules
        assert result == device_view

    def test_interfaces_processed_into_ports_chassis(self):
        self._device_view()
        device = self._device()
        for name in ("GigabitEthernet0/0/1", "GigabitEthernet0/0/2"):
            Interface.objects.create(device=device, name=name, type="1000base-t")

        _dv, _modules, ports_chassis, _device_view = prepare(device, self.user)

        stylenames = [i.stylename for i in ports_chassis["test-switch"]]
        assert "gigabitethernet0-1" in stylenames
        assert "gigabitethernet0-2" in stylenames

    def test_virtual_interfaces_excluded(self):
        self._device_view()
        device = self._device()
        Interface.objects.create(device=device, name="Loopback0", type="virtual")
        Interface.objects.create(
            device=device, name="GigabitEthernet0/0/1", type="1000base-t"
        )

        _dv, _modules, ports_chassis, _device_view = prepare(device, self.user)

        items = ports_chassis.get("test-switch", [])
        assert len(items) == 1
        assert items[0].stylename == "gigabitethernet0-1"

    def test_virtual_chassis_scopes_css(self):
        self._device_view(".deviceview.area { }")
        chassis = VirtualChassis.objects.create(name="vc")
        member1, _member2 = (
            self._device(f"sw{position}", virtual_chassis=chassis, vc_position=position)
            for position in (1, 2)
        )

        dv, _modules, _ports_chassis, _device_view = prepare(member1, self.user)

        assert ".area.d1" in dv[1]
        assert ".area.d2" in dv[2]

    def test_virtual_chassis_missing_member_device_view_returns_none(self):
        self._device_view()
        chassis = VirtualChassis.objects.create(name="vc")
        member = self._device("sw1", virtual_chassis=chassis, vc_position=1)
        other_type = DeviceType.objects.create(
            manufacturer=self.device_type.manufacturer, model="Other", slug="other"
        )
        Device.objects.create(
            name="sw2",
            device_type=other_type,
            site=self.site,
            role=self.role,
            virtual_chassis=chassis,
            vc_position=2,
        )

        assert prepare(member, self.user) == (None, None, None, None)


# ---------------------------------------------------------------------------
# device_height_px — height calculation
# ---------------------------------------------------------------------------


class TestDeviceHeightPx:
    def _make_device_type(self, u_height):
        return types.SimpleNamespace(u_height=u_height)

    def test_1u(self):
        from netbox_device_view.utils import device_height_px

        assert device_height_px(self._make_device_type(1)) == 42

    def test_2u(self):
        from netbox_device_view.utils import device_height_px

        assert device_height_px(self._make_device_type(2)) == 84

    def test_formula(self):
        # height = u * 2 * 20 + u * 2 = u * 42
        from netbox_device_view.utils import device_height_px

        for u in range(1, 6):
            assert device_height_px(self._make_device_type(u)) == u * 42


class TestActivePanelKey:
    """
    active_panel_key() identifies which panel (in a multi-panel virtual
    chassis view) corresponds to the specific device the user navigated
    to, so the template can highlight it (issue #24).
    """

    def test_standalone_device_returns_none(self):
        from netbox_device_view.utils import active_panel_key

        dev = types.SimpleNamespace(virtual_chassis=None, vc_position=None)
        assert active_panel_key(dev) is None

    def test_virtual_chassis_member_returns_vc_position(self):
        from netbox_device_view.utils import active_panel_key

        dev = types.SimpleNamespace(virtual_chassis=MagicMock(), vc_position=2)
        assert active_panel_key(dev) == 2

    def test_virtual_chassis_member_position_zero(self):
        # vc_position=0 is falsy but a valid position -- must not be
        # coerced to None or any other sentinel.
        from netbox_device_view.utils import active_panel_key

        dev = types.SimpleNamespace(virtual_chassis=MagicMock(), vc_position=0)
        assert active_panel_key(dev) == 0
