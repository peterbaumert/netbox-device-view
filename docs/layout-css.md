# CSS Layout (legacy)

The CSS layout is the original format for defining a DeviceView. It is still fully supported, but [YAML layouts](layout-yaml.md) are preferred for new DeviceViews.

---

## Format

Fill the **Grid Template Area** field with a raw CSS block. The grid has **32 columns** and uses **20 px cells**.

```css
/* C9300-24T */
.deviceview.area {
    grid-template-areas:
    "x x x x x x x x x x x x x x gigabitethernet0-1 gigabitethernet0-3 ... z z z z z"
    "x x x x x x x x x x x x x x gigabitethernet0-2 gigabitethernet0-4 ... z z z z z";
}
```

**Reserved placeholder names:**

| Name | Meaning |
|---|---|
| `x` | Leading blank (left side of chassis) |
| `z` | Trailing blank (right side of chassis) |
| `s0`–`s99` | Interior spacer columns |

Each named area must match the `stylename` derived from a port or interface. See [How It Works](how-it-works.md) for the derivation rules.

---

## Module variants

To change the layout when a module is installed, add an additional CSS rule scoped with the module model class:

```css
.deviceview.area {
    /* base layout */
    grid-template-areas: "...";
}

.deviceview.moduleMY-MODULE-MODEL.area {
    /* layout with expansion module ports */
    grid-template-areas: "...";
}
```

---

## Ready-made CSS examples

Pre-built CSS files are available in [`examples/`](https://github.com/peterbaumert/netbox-device-view/tree/main/examples).

---

## Migrating to YAML

Run the bundled management command to convert existing CSS layouts to YAML automatically:

```bash
cd /opt/netbox/netbox
python manage.py css_to_yaml
```

This writes the converted YAML into the **YAML Layout** field for every DeviceView that has a CSS layout but no YAML layout. The original CSS is left untouched so you can roll back by clearing the YAML field.

!!! tip
    After migrating, set **Render Mode → SVG** to take advantage of the SVG renderer.
