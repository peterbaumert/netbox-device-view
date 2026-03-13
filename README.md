# NetBox Device View Plugin

![Version](https://img.shields.io/pypi/v/netbox-device-view)
![Downloads](https://img.shields.io/pypi/dm/netbox-device-view)

Renders a visual CSS grid representation of a device's physical ports and interfaces directly on the NetBox device detail page.

![example](https://github.com/peterbaumert/netbox-device-view/blob/main/docs/example_view.png?raw=true)

## Requirements

- Python 3.12+
- NetBox 3.5+ (see [COMPATIBILITY.md](COMPATIBILITY.md))

## Installation

Install from PyPI into the NetBox virtual environment:

```bash
source /opt/netbox/venv/bin/activate
pip install netbox-device-view
```

To ensure the plugin is reinstalled on future NetBox upgrades, add it to `local_requirements.txt`:

```bash
echo netbox-device-view >> /opt/netbox/local_requirements.txt
```

Run the migration and collect static files:

```bash
cd /opt/netbox/netbox
python manage.py migrate netbox_device_view
python manage.py collectstatic --no-input
```

## Configuration

Enable the plugin in your NetBox `configuration.py`:

```python
PLUGINS = ["netbox_device_view"]

PLUGINS_CONFIG = {
    "netbox_device_view": {
        # Show the port grid inline on the main device tab (default: False)
        # When False, the grid is only shown on a dedicated "Device View" tab
        "show_on_device_tab": False,
        # Where to render the grid when show_on_device_tab is True (default: "bottom")
        # "bottom" — below main device content (full_width_page)
        # "top"    — above main device content (alerts area)
        "device_tab_position": "bottom",
    },
}
```

## How It Works

For each **Device Type** you create a **DeviceView** object (at *Plugins → Device Views → Add*) containing a CSS `grid-template-areas` definition. When you visit a device of that type, the plugin renders every interface and port as a clickable cell in the grid.

### Port/Interface name → CSS grid-area name

The plugin derives a `stylename` for each interface and port, which must match the area names in your CSS:

| Interface name | Stylename |
|----------------|-----------|
| `GigabitEthernet0/0/1` | `gigabitethernet0-1` |
| `TenGigabitEthernet1/1/4` | `tengigabitethernet1-4` |
| `GigabitEthernet0/1` | `gigabitethernet-1` |
| `Management0` | `management-0` |
| `eth0` | `eth-0` |
| `1` (numeric only) | `p1` |

**Rules:**
- Names are lowercased
- For the 3-part Cisco format `Type{dev}/{module}/{port}`, stylename is `type{module}-{port}`
- For the 2-part format `Type{dev}/{port}`, stylename is `type-{port}`
- All other names: non-alphanumeric/dot characters are replaced with `-`
- Purely numeric stylenames are prefixed with `p`
- Virtual and LAG interfaces are skipped

### CSS layout format

The `grid_template_area` field stores raw CSS. The grid has **32 columns** and uses **20 px cells**.

Placeholder names:
- `x` — leading empty cell
- `z` — trailing empty cell
- `s0`–`s99` — interior spacer cells

Example for a Cisco C9300-24T with an optional 8x 10G module (more in the [`examples/`](examples/) folder):

```css
/* C9300-24T */
.deviceview.area {
	grid-template-areas:
	"x x x x x x x x x x x x x x gigabitethernet0-1 gigabitethernet0-3 gigabitethernet0-5 gigabitethernet0-7 gigabitethernet0-9 gigabitethernet0-11 s0 gigabitethernet0-13 gigabitethernet0-15 gigabitethernet0-17 gigabitethernet0-19 gigabitethernet0-21 gigabitethernet0-23 z z z z z"
	"x x x x x x x x x x x x x x gigabitethernet0-2 gigabitethernet0-4 gigabitethernet0-6 gigabitethernet0-8 gigabitethernet0-10 gigabitethernet0-12 s0 gigabitethernet0-14 gigabitethernet0-16 gigabitethernet0-18 gigabitethernet0-20 gigabitethernet0-22 gigabitethernet0-24 z z z z z";
}

/* C9300-24T with C9300-NM-8X */
.deviceview.moduleC9300-NM-8X.area {
	grid-template-areas:
	"x x x x x x x x x x x x x x gigabitethernet0-1 gigabitethernet0-3 gigabitethernet0-5 gigabitethernet0-7 gigabitethernet0-9 gigabitethernet0-11 s0 gigabitethernet0-13 gigabitethernet0-15 gigabitethernet0-17 gigabitethernet0-19 gigabitethernet0-21 gigabitethernet0-23 s1 tengigabitethernet1-1 tengigabitethernet1-3 tengigabitethernet1-5 tengigabitethernet1-7"
	"x x x x x x x x x x x x x x gigabitethernet0-2 gigabitethernet0-4 gigabitethernet0-6 gigabitethernet0-8 gigabitethernet0-10 gigabitethernet0-12 s0 gigabitethernet0-14 gigabitethernet0-16 gigabitethernet0-18 gigabitethernet0-20 gigabitethernet0-22 gigabitethernet0-24 s1 tengigabitethernet1-2 tengigabitethernet1-4 tengigabitethernet1-6 tengigabitethernet1-8";
}
```

### Module variants

If a device has an installed module, an additional CSS class matching the module model is added. Define a separate rule to override the layout when that module is present (see the example above).

### Virtual Chassis

For virtual chassis devices, each member's CSS areas are scoped with a `.d{vc_position}` suffix automatically. Define per-member layouts using those suffixes:

```css
/* Member at vc_position 1 */
.deviceview.area.d1 {
    grid-template-areas: "gigabitethernet0-1 gigabitethernet0-2";
}

/* Member at vc_position 2 */
.deviceview.area.d2 {
    grid-template-areas: "gigabitethernet0-1 gigabitethernet0-2";
}
```

More ready-made layouts for Cisco C2960X, C8300, C9200, C9300, generic patch panels, and Ubiquiti switches are in the [`examples/`](examples/) folder.

## Troubleshooting

**Tab not showing / badge count is zero**
No DeviceView exists for this device's type. Go to *Plugins → Device Views → Add* and create one.

**A port is not appearing in the grid**
The port's stylename is not referenced in the CSS. Check what stylename the plugin derives: it follows the rules in the table above. Add a corresponding area name to your `grid-template-areas`.

**Layout looks wrong after adding a module**
Add a `.deviceview.module{ModelName}.area` rule to your CSS that includes the new module ports.

**Virtual chassis ports missing**
Ensure your CSS uses `.area.d{vc_position}` scoped selectors. Each VC member needs its own rule.
