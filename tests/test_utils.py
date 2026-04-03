"""
Tests for netbox_device_view.utils

process_interfaces() and process_ports() derive a `stylename` attribute on each
object and bucket them by device key in ports_chassis.  prepare() orchestrates
those calls around the DB lookup.
"""

import types
from unittest.mock import MagicMock, patch


from netbox_device_view.utils import process_interfaces, process_ports, prepare

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
# prepare() — DB interaction mocked
# ---------------------------------------------------------------------------


class TestPrepare:
    """prepare() wires the DB lookup + process_* calls together."""

    def _make_device(self, vc=None):
        """Minimal device mock for a non-VC device."""
        dev = MagicMock()
        dev.virtual_chassis = vc
        dev.name = "test-switch"
        dev.id = 1
        dev.device_type = MagicMock()
        dev.interfaces.all.return_value = []
        dev.modules.all.return_value = []
        dev.frontports.all.return_value = []
        dev.rearports.all.return_value = []
        return dev

    @patch("netbox_device_view.utils.ConsolePort")
    @patch("netbox_device_view.utils.DeviceView")
    def test_no_device_view_returns_none_triple(self, MockDV, MockCP):
        from django.core.exceptions import ObjectDoesNotExist

        MockDV.objects.get.side_effect = ObjectDoesNotExist
        dev = self._make_device()
        dv, modules, ports_chassis, device_view = prepare(dev)
        assert dv is None
        assert modules is None
        assert ports_chassis is None
        assert device_view is None

    @patch("netbox_device_view.utils.ConsolePort")
    @patch("netbox_device_view.utils.DeviceView")
    def test_single_device_returns_dicts(self, MockDV, MockCP):
        MockDV.objects.get.return_value.grid_template_area = ".area { }"
        MockCP.objects.filter.return_value = []
        dev = self._make_device()

        dv, modules, ports_chassis, device_view = prepare(dev)

        assert dv == {1: ".area { }"}
        assert 1 in modules
        assert device_view is MockDV.objects.get.return_value

    @patch("netbox_device_view.utils.ConsolePort")
    @patch("netbox_device_view.utils.DeviceView")
    def test_interfaces_processed_into_ports_chassis(self, MockDV, MockCP):
        MockDV.objects.get.return_value.grid_template_area = ".area { }"
        MockCP.objects.filter.return_value = []

        dev = self._make_device()
        dev.interfaces.all.return_value = [
            make_iface("GigabitEthernet0/0/1"),
            make_iface("GigabitEthernet0/0/2"),
        ]

        dv, modules, ports_chassis, device_view = prepare(dev)

        assert "test-switch" in ports_chassis
        stylenames = [i.stylename for i in ports_chassis["test-switch"]]
        assert "gigabitethernet0-1" in stylenames
        assert "gigabitethernet0-2" in stylenames

    @patch("netbox_device_view.utils.ConsolePort")
    @patch("netbox_device_view.utils.DeviceView")
    def test_virtual_interfaces_excluded(self, MockDV, MockCP):
        MockDV.objects.get.return_value.grid_template_area = ".area { }"
        MockCP.objects.filter.return_value = []

        dev = self._make_device()
        dev.interfaces.all.return_value = [
            make_iface("Loopback0", iface_type="virtual"),
            make_iface("GigabitEthernet0/0/1", iface_type="1000base-t"),
        ]

        dv, modules, ports_chassis, device_view = prepare(dev)

        items = ports_chassis.get("test-switch", [])
        assert len(items) == 1
        assert items[0].stylename == "gigabitethernet0-1"

    @patch("netbox_device_view.utils.ConsolePort")
    @patch("netbox_device_view.utils.DeviceView")
    def test_virtual_chassis_scopes_css(self, MockDV, MockCP):
        MockDV.objects.get.return_value.grid_template_area = ".deviceview.area { }"
        MockCP.objects.filter.return_value = []

        member1 = MagicMock()
        member1.vc_position = 1
        member1.id = 1
        member1.device_type = MagicMock()
        member1.interfaces.all.return_value = []
        member1.modules.all.return_value = []

        member2 = MagicMock()
        member2.vc_position = 2
        member2.id = 2
        member2.device_type = MagicMock()
        member2.interfaces.all.return_value = []
        member2.modules.all.return_value = []

        dev = self._make_device(vc=MagicMock())
        dev.virtual_chassis.members.all.return_value = [member1, member2]

        dv, modules, ports_chassis, device_view = prepare(dev)

        assert ".area.d1" in dv[1]
        assert ".area.d2" in dv[2]

    @patch("netbox_device_view.utils.ConsolePort")
    @patch("netbox_device_view.utils.DeviceView")
    def test_virtual_chassis_missing_member_device_view_returns_none(
        self, MockDV, MockCP
    ):
        from django.core.exceptions import ObjectDoesNotExist

        # Second member has no DeviceView → ObjectDoesNotExist → whole prepare returns None
        MockDV.objects.get.side_effect = ObjectDoesNotExist
        MockCP.objects.filter.return_value = []

        member = MagicMock()
        member.vc_position = 1
        member.id = 1
        member.device_type = MagicMock()
        member.interfaces.all.return_value = []
        member.modules.all.return_value = []

        dev = self._make_device(vc=MagicMock())
        dev.virtual_chassis.members.all.return_value = [member]

        dv, modules, ports_chassis, device_view = prepare(dev)

        assert dv is None
        assert modules is None
        assert ports_chassis is None
        assert device_view is None


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
