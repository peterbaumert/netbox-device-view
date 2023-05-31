from extras.plugins import PluginConfig

class NetBoxDeviceViewConfig(PluginConfig):
    name = 'netbox_device_view'
    verbose_name = ' NetBox Device View'
    description = 'Show Device Views'
    version = '0.1'
    base_url = 'device_view'
    
config = NetBoxDeviceViewConfig