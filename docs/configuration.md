# Configuration

Add a `PLUGINS_CONFIG` entry to your NetBox `configuration.py` to customise the plugin's behaviour.

All settings are optional — the defaults work out of the box.

```python
PLUGINS_CONFIG = {
    "netbox_device_view": {
        # Show the port grid inline on the main device tab (default: False).
        # When False, the grid is shown only on a dedicated "Device View" tab.
        "show_on_device_tab": False,

        # Where to render the grid when show_on_device_tab is True (default: "bottom").
        # "bottom" — below the main device content (full_width_page)
        # "top"    — above the main device content (alerts area)
        "device_tab_position": "bottom",
    },
}
```

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `show_on_device_tab` | `bool` | `False` | Render the port grid inline on the main device tab in addition to the dedicated tab |
| `device_tab_position` | `str` | `"bottom"` | Position when `show_on_device_tab` is `True`. `"bottom"` or `"top"` |
