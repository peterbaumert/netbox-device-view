from netbox.plugins import PluginMenuButton, PluginMenuItem
from netbox.choices import ButtonColorChoices

deviceview_buttons = [
    PluginMenuButton(
        link="plugins:netbox_device_view:deviceview_add",
        title="Add",
        icon_class="mdi mdi-plus-thick",
        color=ButtonColorChoices.GREEN,
    ),
    PluginMenuButton(
        link="plugins:netbox_device_view:deviceview_import",
        title="Import",
        icon_class="mdi mdi-upload",
    ),
]

menu_items = (
    PluginMenuItem(
        link="plugins:netbox_device_view:deviceview_list",
        link_text="Device Views",
        buttons=deviceview_buttons,
    ),
)
