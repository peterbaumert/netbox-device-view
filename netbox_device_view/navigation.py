from extras.plugins import PluginMenuButton, PluginMenuItem
from utilities.choices import ButtonColorChoices

deviceview_buttons = [
    PluginMenuButton(
        link="plugins:netbox_device_view:deviceview_add",
        title="Add",
        icon_class="mdi mdi-plus-thick",
        color=ButtonColorChoices.GREEN,
    )
]

menu_items = (
    PluginMenuItem(
        link="plugins:netbox_device_view:deviceview_list",
        link_text="Device Views",
        buttons=deviceview_buttons,
    ),
)
