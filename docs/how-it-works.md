# How It Works

For each **Device Type** you create a **DeviceView** object (at *Plugins → Device Views → Add*) with either a [YAML layout](layout-yaml.md) or a legacy [CSS layout](layout-css.md).

When you visit a device of that type, the plugin:

1. Looks up the DeviceView for the device's type
2. Collects all interfaces, front ports, rear ports, and console ports from the device
3. Derives a `stylename` for each port (see below)
4. Renders the layout using the configured render mode (CSS Grid or SVG)
5. Applies port status colours based on connection state

---

## Port / Interface name → stylename

Each interface and port is mapped to a `stylename` used as the CSS grid-area name (and SVG `data-stylename` attribute). The derivation rules are:

| Interface name | Stylename |
|---|---|
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
- All other names: non-alphanumeric/dot characters replaced with `-`
- Purely numeric stylenames are prefixed with `p`
- Virtual and LAG interfaces are skipped

---

## Render modes

A DeviceView has a **Render Mode** field:

| Mode | Description |
|---|---|
| `css` (default) | CSS Grid renderer — works with both YAML and legacy CSS layouts |
| `svg` | SVG renderer — requires a YAML layout; produces a scalable `<svg>` element |

See [SVG Renderer](svg-renderer.md) for full details on SVG mode.
