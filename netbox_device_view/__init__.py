from netbox.plugins import PluginConfig
from importlib.metadata import metadata

metadata = metadata("netbox_device_view")


class NetBoxDeviceViewConfig(PluginConfig):
    name = metadata.get("Name").replace("-", "_")
    verbose_name = metadata.get("Summary")
    description = "Plugin to visualize device ports"
    version = metadata.get("Version")
    author = metadata.get("Author")
    base_url = "device_view"
    required_settings = []
    default_settings = {"show_on_device_tab": False}


config = NetBoxDeviceViewConfig
