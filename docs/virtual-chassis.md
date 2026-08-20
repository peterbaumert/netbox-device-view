# Virtual Chassis

When a device is part of a NetBox Virtual Chassis, the plugin renders each member as a separate panel within the same device view page.

---

## How it works

For Virtual Chassis devices:

- The plugin iterates over all VC members in `vc_position` order
- Each member's DeviceView CSS is scoped with a `.d{vc_position}` suffix to avoid selector conflicts
- Each member's ports are collected and displayed under a "Device {vc_position}" heading

---

## CSS scoping

For the CSS renderer, each member's grid-area selectors are automatically rewritten:

```css
/* Member at vc_position=1 */
.deviceview.area.d1 {
    grid-template-areas: "...";
}

/* Member at vc_position=2 */
.deviceview.area.d2 {
    grid-template-areas: "...";
}
```

This happens automatically — you do not need to write scoped CSS yourself. The plugin applies the `.d{pos}` rewrite at render time using the base DeviceView CSS for each member's device type.

---

## Limitations

- **SVG mode works for Virtual Chassis devices** when every member's device type has an SVG-capable DeviceView (Render Mode → SVG, non-empty YAML Layout) — one panel is rendered per member, in rack order, using that member's own DeviceView.
- Each VC member must have its own DeviceView configured for its device type.
- If **any** member is missing a DeviceView (for either CSS or SVG rendering), the whole device's view fails to render — there is no partial/per-member fallback.
