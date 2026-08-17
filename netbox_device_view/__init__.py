from importlib.metadata import metadata
from typing import ClassVar

from netbox.plugins import PluginConfig

metadata = metadata("netbox_device_view")


class NetBoxDeviceViewConfig(PluginConfig):
    name = metadata.get("Name").replace("-", "_")
    verbose_name = metadata.get("Summary")
    description = "Plugin to visualize device ports"
    version = metadata.get("Version")
    author = metadata.get("Author")
    base_url = "device_view"
    required_settings: ClassVar[list[str]] = []
    default_settings: ClassVar[dict[str, object]] = {
        "show_on_device_tab": False,
        "device_tab_position": "bottom",  # "top" | "bottom"
    }


config = NetBoxDeviceViewConfig
