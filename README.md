# NetBox Device View Plugin

![Version](https://img.shields.io/pypi/v/netbox-device-view)
![Downloads](https://img.shields.io/pypi/dm/netbox-device-view)
![CI](https://github.com/peterbaumert/netbox-device-view/actions/workflows/test.yml/badge.svg)
![License](https://img.shields.io/github/license/peterbaumert/netbox-device-view)

Renders a visual CSS grid representation of a device's physical ports and interfaces directly on the NetBox device detail page. An SVG renderer is also available for YAML-based layouts.

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

For each **Device Type** you create a **DeviceView** object (at *Plugins → Device Views → Add*) with either a **YAML layout** or a raw **CSS layout**. When you visit a device of that type, the plugin renders every interface and port as a clickable cell in the grid.

### Port/Interface name → CSS grid-area name

The plugin derives a `stylename` for each interface and port:

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

---

## Layout formats

A DeviceView supports two layout formats. **YAML is preferred** — it is easier to write, validate, and maintain. Legacy CSS is still fully supported for backward compatibility.

### Render modes

A DeviceView also has a **Render Mode** field:

| Mode | Description |
|------|-------------|
| `css` (default) | CSS Grid renderer — works with both YAML and legacy CSS layouts |
| `svg` | SVG renderer — requires a YAML layout; produces a scalable `<svg>` element |

SVG mode produces the same port positions as CSS mode (using `canvas.cell_size` for coordinates) and supports front/rear faces, module variants, hover tooltips, and click targets. Virtual Chassis devices always fall back to CSS mode.

### YAML layout (recommended)

Fill the **YAML Layout** field with a YAML document describing the device layout. The plugin compiles it to CSS at render time.

Example for a Cisco C9300-24T with an optional 8x 10G expansion module:

```yaml
version: 1

meta:
  description: "Cisco Catalyst 9300-24T"

canvas:
  columns: 32
  rows: 2

views:
  front:
    rows:
      - blank: 14
      - group:
          spacer: 1
          sections:
            - sequence:
                kind: interface
                prefix: "gigabitethernet1-"
                start: 1
                count: 12
                pattern: top-odd
            - sequence:
                kind: interface
                prefix: "gigabitethernet1-"
                start: 13
                count: 12
                pattern: top-odd
      - spacer: 4

variants:
  C9300-NM-8X:
    match: module
    rows:
      - blank: 14
      - group:
          spacer: 1
          sections:
            - sequence:
                kind: interface
                prefix: "gigabitethernet1-"
                start: 1
                count: 12
                pattern: top-odd
            - sequence:
                kind: interface
                prefix: "gigabitethernet1-"
                start: 13
                count: 12
                pattern: top-odd
            - sequence:
                kind: interface
                prefix: "tengigabitethernet1-"
                start: 1
                count: 8
                pattern: top-odd
```

See [`docs/yaml-layout-schema.md`](docs/yaml-layout-schema.md)

### CSS layout (legacy)

Fill the **Grid Template Area** field with raw CSS. The grid has **32 columns** and uses **20 px cells**.

Placeholder names: `x` — leading blank, `z` — trailing blank, `s0`–`s99` — interior spacers.

```css
/* C9300-24T */
.deviceview.area {
    grid-template-areas:
    "x x x x x x x x x x x x x x gigabitethernet0-1 gigabitethernet0-3 ... z z z z z"
    "x x x x x x x x x x x x x x gigabitethernet0-2 gigabitethernet0-4 ... z z z z z";
}
```

See [`examples/`](examples/) for ready-made CSS files.

> **Precedence:** if a DeviceView has a YAML layout, it is used and the CSS field is ignored.

### Migrating from CSS to YAML

> [!IMPORTANT]
> If you have existing DeviceViews with CSS `grid_template_area` layouts and want to switch to the SVG renderer, run the bundled management command to convert them automatically:
>
> ```bash
> cd /opt/netbox/netbox
> python manage.py css_to_yaml
> ```
>
> This converts every DeviceView that has a CSS layout but no YAML layout, writes the result into the **YAML Layout** field, and leaves the original CSS untouched (so you can roll back by clearing the YAML field). After running it, set **Render Mode → SVG** on the DeviceViews you want to upgrade.

### Module variants

When a device has an installed module, an extra CSS class matching the module model is added to the device div. In YAML, declare a `variants:` block. In CSS, add a `.deviceview.module{ModelName}.area` rule.

### Virtual Chassis

For virtual chassis devices, each member's CSS areas are scoped with a `.d{vc_position}` suffix automatically. YAML-rendered CSS is scoped the same way.

## Troubleshooting

**Tab not showing / badge count is zero**
No DeviceView exists for this device's type. Go to *Plugins → Device Views → Add* and create one.

**A port is not appearing in the grid**
The port's stylename is not referenced in the CSS. Check what stylename the plugin derives: it follows the rules in the table above. Add a corresponding area name to your `grid-template-areas`.

**Layout looks wrong after adding a module**
Add a `.deviceview.module{ModelName}.area` rule to your CSS that includes the new module ports.

**Virtual chassis ports missing**
Ensure your CSS uses `.area.d{vc_position}` scoped selectors. Each VC member needs its own rule.

**SVG mode shows nothing / falls back to CSS**
SVG mode requires a YAML layout. If the DeviceView has no YAML layout, or the device is part of a Virtual Chassis, it silently falls back to CSS rendering.

**Port status colours not showing in SVG mode**
Status classes (`bg-success`, `bg-secondary`, `bg-danger`) are applied by JavaScript using the `data-stylename` attribute. Ensure the static file `netbox_device_view/css/device_view_svg.css` is collected (`python manage.py collectstatic`).
