# How It Works

For each **Device Type** you create a **DeviceView** object (at *Plugins → Device Views → Add*) with either a [YAML layout](layout-yaml.md) or a legacy [CSS layout](layout-css.md).

When you visit a device of that type, the plugin:

1. Looks up the DeviceView for the device's type
2. Collects all interfaces, front ports, rear ports, console ports, and console-server ports from the device, including the ports of installed modules. For a virtual chassis member, it collects only the interfaces, console ports and console-server ports. It uses only the objects that you can view (see [Object permissions](#object-permissions))
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
- Front, rear, console and console-server ports always use the replacement rule for other names: `ttyS1` becomes `ttys1`, and `Async0/1/0` becomes `async0-1-0`
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

---

## Object permissions

The Device View tab and the device page panel show only the objects that you can view. NetBox object permissions and their constraints apply to each object:

- The Device View tab of a device that you cannot view is not found (`404`).
- A virtual chassis member that you cannot view has no panel. The panel title shows the virtual chassis name only when you can view the virtual chassis.
- A DeviceView that you cannot view counts as missing. The device page then has no Device View tab.
- A port that you cannot view is not in the panel. A peer that you cannot view is not in the port's popover.
- A cable that you cannot view gives no cable colour.
- A module that you cannot view does not select a module variant.

The tab needs the **view** permission on **Device View**. The [rendered-layout API](rendered-layout-api.md#object-permissions) uses the same rule.
