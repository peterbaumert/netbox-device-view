# Troubleshooting

## Tab not showing / badge count is zero

No DeviceView exists for this device's type.

Go to *Plugins → Device Views → Add* and create one for the device type.

---

## A port is not appearing in the grid

The port's `stylename` is not referenced in the layout.

Check what stylename the plugin derives — it follows the rules in [How It Works](how-it-works.md). Then add a matching area name to your YAML or CSS layout.

---

## Layout looks wrong after adding a module

Add a `variants:` block to your YAML layout (or a `.deviceview.module{ModelName}.area` rule in CSS) that includes the new module ports. See [YAML Layout — Variant definition](layout-yaml.md#variant-definition).

---

## Virtual chassis ports missing

Ensure each VC member's device type has a DeviceView configured. The CSS scoping (`.d{vc_position}`) is applied automatically — no manual scoping is needed.

---

## SVG mode shows nothing / falls back to CSS

SVG mode requires:

1. The DeviceView has **Render Mode → SVG** set
2. The DeviceView has a non-empty **YAML Layout** field
3. The device is **not** part of a Virtual Chassis

If any of these conditions are not met, the plugin silently falls back to CSS rendering.

---

## Port status colours not showing in SVG mode

Status classes (`bg-success`, `bg-warning`, `bg-secondary`, `bg-danger`) are applied by JavaScript at page load using the `data-stylename` attribute. Check that:

1. The static file `netbox_device_view/css/device_view_svg.css` has been collected:
   ```bash
   python manage.py collectstatic --no-input
   ```
2. JavaScript is not blocked by a browser extension or Content Security Policy.

---

## Partial connection dot not visible

The yellow dot overlay is injected by JavaScript when a port has `link_peers` but no `connected_endpoints`. Verify that:

- The port's cable is actually attached in NetBox
- The cable path genuinely has no far-end device (check via the port's trace view)
- JavaScript is running without errors (check the browser console)
