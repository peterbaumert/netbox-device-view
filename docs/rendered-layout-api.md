# Rendered Layout API

A read-only, versioned JSON endpoint that composes a device's SVG layout — structure, port colours, connection state, cable colour, and peer summaries — into a single response. It exists for API clients (e.g. the [netbox-companion](https://github.com/peterbaumert/netbox-companion) mobile app) that render the layout natively instead of embedding NetBox's own HTML/JavaScript device-view page.

The existing [DeviceView CRUD API](configuration.md) (`/api/plugins/device_view/device-view/`) only exposes layout *configuration* (the raw YAML/CSS a device type uses). It does not compose that configuration against a specific device's real interfaces, cables, and connection state — that composition happens in the web UI via [`ports.html`'s inline JavaScript](how-it-works.md). This endpoint moves that composition server-side so API clients don't have to reimplement it.

---

## Endpoint

```
GET /api/plugins/device_view/devices/<device_id>/rendered-layout/
```

Token-authenticated, like the rest of the plugin's API. Requires `dcim.view_device` (returns `403` without it) and respects NetBox's normal object-level permission scoping (a device outside the requesting user's permitted scope returns a normal `404`, indistinguishable from a device that doesn't exist — NetBox's API does this everywhere to avoid leaking object existence).

| Condition | Response |
|---|---|
| Device does not exist, or exists but is outside the user's object-level permission scope | `404` |
| User lacks `dcim.view_device` entirely | `403` |
| Anonymous / unauthenticated | `401`/`403` (per NetBox's `LOGIN_REQUIRED` setting) |
| Device exists and is visible, but has no `DeviceView` for its device type (or, for a Virtual Chassis, any member's device type) | `200`, `available: false`, `reason: "no_layout"` |
| A `DeviceView` exists but has no YAML layout (CSS-only/legacy) | `200`, `available: false`, `reason: "svg_layout_required"` |
| A YAML layout exists but fails to parse | `200`, `available: false`, `reason: "invalid_layout"` (never a traceback) |
| Everything resolves | `200`, `available: true`, one or more `panels` |

---

## Response schema (`schema_version: 1`)

```json
{
  "schema_version": 1,
  "available": true,
  "reason": null,
  "device_id": 123,
  "render_mode": "svg",
  "panels": [
    {
      "key": "front",
      "label": "Front",
      "width": 218,
      "height": 36,
      "svg": "<svg xmlns=\"http://www.w3.org/2000/svg\" ...>...</svg>",
      "hotspots": [
        {
          "object_type": "dcim.interface",
          "object_id": 456,
          "name": "GigabitEthernet0/1",
          "x": 6, "y": 6, "width": 24, "height": 24,
          "enabled": true,
          "connection_state": "connected",
          "cable_color": "ff0000",
          "peer_labels": ["dev1 | GigabitEthernet0/2"],
          "trace_supported": true
        }
      ]
    }
  ]
}
```

### Top level

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | Always `1` for this version. A future breaking change bumps this rather than mutating the shape in place. |
| `available` | bool | `false` for any of the reasons above. |
| `reason` | string \| null | `"no_layout"` \| `"svg_layout_required"` \| `"invalid_layout"` \| `null` (only meaningful when `available` is `false`). |
| `device_id` | int | Echoes the requested device's primary key. |
| `render_mode` | string | Always `"svg"` — this endpoint is SVG-only regardless of the DeviceView's own `render_mode` field (see below). |
| `panels` | array | One entry per rendered face. Empty when `available` is `false`. |

### Panel

| Field | Type | Notes |
|---|---|---|
| `key` | string | `"front"` / `"rear"` for a normal device or patch panel. For a Virtual Chassis, `"member-{device_id}-front"` / `"member-{device_id}-rear"` — one set per member. |
| `label` | string | Human-readable panel label, e.g. `"Front"`, `"Rear (member-2)"`. |
| `width`, `height` | int | The SVG's pixel dimensions — matches the `viewBox`/`width`/`height` on the embedded `<svg>`. |
| `svg` | string | Self-contained SVG markup (see [SVG output](#svg-output) below). |
| `hotspots` | array | One entry per port/interface/console-port element that has a matching real NetBox component. |

### Hotspot

| Field | Type | Notes |
|---|---|---|
| `object_type` | string | `"dcim.interface"` \| `"dcim.consoleport"` \| `"dcim.frontport"` \| `"dcim.rearport"` — Django's `app_label.model_name`, so it's safe to route on directly. Power ports are not yet collected by the plugin's layout pipeline (`utils.prepare_svg`), so they never appear here. |
| `object_id` | int | The real NetBox object's primary key — use this (with `object_type`) to navigate, not a URL. |
| `name` | string | The component's real name, e.g. `"GigabitEthernet0/1"`. |
| `x`, `y`, `width`, `height` | int | Hotspot geometry in the same coordinate space as the panel's `svg`/`viewBox` — apply one transform to both. |
| `enabled` | bool | Always `true` for port-type components (they have no enabled flag); reflects `Interface.enabled` for interfaces. |
| `connection_state` | string | `"connected"` \| `"partially_connected"` \| `"enabled"` \| `"disabled"`. `"partially_connected"` only applies to interfaces (a cable is attached but the traced path doesn't resolve to a far-end device) — see [SVG Renderer](svg-renderer.md#states). |
| `cable_color` | string \| null | `null` = no cable attached. `""` = cable attached, no colour set (rendered with a diagonal no-colour pattern). `"rrggbb"` = the cable's configured colour. |
| `peer_labels` | array of string | `"{device} | {name}"` per resolved peer (traced endpoint for interfaces, direct cable peer for ports). Empty when unconnected. |
| `trace_supported` | bool | Whether NetBox's REST trace endpoint (`GET /api/dcim/{type}s/{id}/trace/`) exists for this object type. `true` for interfaces and console ports; `false` for front/rear ports, which use `PassThroughPortMixin` and have no `/trace/` action. |

---

## SVG output

Unlike the web UI's SVG (documented in [SVG Renderer](svg-renderer.md)), this endpoint's SVG is built specifically to be safe for renderers with no CSS support and unreliable text layout (e.g. Flutter's `flutter_svg`):

- **No CSS classes.** Every port's fill colour is baked in as an explicit `fill` attribute at composition time — nothing depends on a stylesheet or JavaScript running after the SVG is parsed.
- **No `<text>` elements.** Port/interface labels are not rendered into the SVG at all — they're returned as `hotspot.name` instead. Renderers that can't reliably centre SVG text (a real, documented `flutter_svg` limitation) render labels as native text overlays positioned from hotspot geometry instead.
- **No interactivity attributes** (`<title>`, `tabindex`, `role`, `data-bs-*`) — those are a browser/Bootstrap concern with no equivalent here.
- The same `dv-nocolor-pattern` diagonal-stripe `<defs>` pattern the web renderer uses is included, for cables with no colour set.

If you need to render this SVG in a browser-based tool for debugging, it will look structurally identical to the web UI's SVG but with colours already applied and no labels.

### Why `render_mode` is always `"svg"`

A `DeviceView`'s own `render_mode` field (`css` or `svg`) controls which renderer NetBox's *web* UI uses — it's a per-installation display preference, not a capability flag. This endpoint has no CSS-rendering story at all (there's no equivalent for a mobile client), so it renders SVG whenever a YAML layout exists (`DeviceView.has_yaml_layout`), regardless of what `render_mode` is set to. Only a genuinely CSS-only/legacy DeviceView (no YAML layout at all) returns `svg_layout_required`.

---

## Virtual Chassis

A Virtual Chassis device returns one set of panels per member, in rack order (same ordering the web UI uses), each composed from that member's own device type's `DeviceView`. If **any** member's device type lacks an SVG-capable `DeviceView`, the whole response is `available: false` — there is no partial per-member fallback (this matches the existing `prepare_svg()` behaviour the web UI relies on). See [Virtual Chassis](virtual-chassis.md).

---

## Performance

The composition path (`netbox_device_view.api.rendered_layout`) prefetches cable terminations and cable-trace path objects for every component up front, using the same prefetch shapes NetBox's own `InterfaceViewSet`/`ConsolePortViewSet`/`FrontPortViewSet`/`RearPortViewSet` use (`dcim/api/views.py`) — one request never issues a query per port. This is covered by a regression test (`tests/test_api_rendered_layout.py::QueryCountTests`) that asserts query count doesn't scale with interface count.

No caching (`ETag`/`Last-Modified`) is applied — connection state and cable colour can change at any time, and a stale cached response would show wrong data. This may be revisited if it becomes a real bottleneck.

---

## Compatibility

| Plugin version | Endpoint |
|---|---|
| < 0.4.0 | Not present — `GET .../rendered-layout/` 404s (indistinguishable from a missing device at the HTTP level; a client that has independently confirmed the device exists, e.g. via `/api/dcim/devices/<id>/`, can treat a 404 here as "endpoint not supported by this plugin version"). |
| ≥ 0.4.0 | Present, `schema_version: 1`. |

Clients should treat an unrecognized `schema_version` as unsupported and prompt for a plugin/app update rather than attempting to parse an unknown shape.
