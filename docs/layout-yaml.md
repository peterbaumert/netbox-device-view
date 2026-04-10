# YAML Layout Reference

The YAML layout is the recommended way to define a DeviceView. It is compiled at render time to CSS (CSS mode) or used directly by the SVG renderer.

Fill the **YAML Layout** field on a DeviceView object with a document matching the schema below.

---

## Top-level structure

```yaml
version: 1          # required — must be 1

meta:               # optional
  description: "Human-readable name"
  background: "#d9d9d9"   # default canvas background for all views

canvas:             # optional — defaults shown
  columns: 32       # total grid columns
  rows: 2           # total grid rows
  cell_size: 20     # px per cell used by the SVG renderer
                    # set to 0 to enable patch-panel auto sizing

views:              # required — at least one of: front, rear
  front: <view>
  rear:  <view>

variants:           # optional — module overlays
  "<Module Model Name>": <variant>
```

---

## View definition

A view defines the layout for one face (front or rear). It uses either `rows:` (high-level, recommended) or `elements:` (explicit coordinates).

```yaml
views:
  front:
    background: "#000"    # optional — overrides meta.background for this view
    canvas:               # optional — override canvas for this view only
      columns: 24
    rows:                 # ordered list of row entries (see below)
      - ...
    # OR
    elements:             # explicit element list with at:/span: (see below)
      - ...
    copy_from: front      # copy another view's rows as a base (then override)
```

### Row entries

Each entry in `rows:` is one of:

#### `sequence` — expand a numbered port range

```yaml
- sequence:
    kind: port            # port | interface | console-port | power-port | module-slot
    prefix: "port-"       # string prepended to each port number
    start: 1              # first port number (default: 1)
    count: 24             # how many ports
    step: 1               # increment between port numbers (default: 1)
    pattern: top-odd      # top-odd | top-even | all  (default: top-odd)
    row: 1                # for pattern: all only — target row (default: 1)
```

**Patterns:**

| Pattern | Behaviour |
|---|---|
| `top-odd` | Odd-numbered ports → row 1; even-numbered ports → row 2 (standard 2U Cisco style) |
| `top-even` | Even-numbered ports → row 1; odd-numbered ports → row 2 (inverted) |
| `all` | All ports in a single row (patch-panel style); use `row: 2` to place in bottom row |

#### `group` — multiple sequences separated by spacers

```yaml
- group:
    spacer: 1             # number of spacer columns between sections
    sections:
      - sequence: ...
      - sequence: ...
      - elements: [...]   # inline explicit elements
      - blank: 2          # insert N blank columns in this section
```

#### `spacer` — insert N spacer columns (spanning all rows)

```yaml
- spacer: 1
```

Spacers use auto-generated keys (`s1`, `s2`, …) and appear as empty visual gaps.

#### `blank` — insert N blank decorative columns (spanning all rows)

```yaml
- blank: 14
```

Blanks also use auto-generated keys (`x1`, `x2`, …). Used for the left-hand chassis art on switches.

#### `elements` — explicit list for a single row

```yaml
- elements:
    - kind: interface
      key: "tengigabitethernet0-1"
    - kind: blank
      key: "y"
    - "some-port-key"     # shorthand string: kind inferred from key name
```

---

## Explicit element format (`elements:` at view level)

For precise control, skip `rows:` and use `elements:` directly with explicit coordinates:

```yaml
views:
  front:
    elements:
      - kind: port
        key: "port-1"
        at:
          row: 1
          col: 1
        span:
          rows: 1       # default: 1
          cols: 1       # default: 1
        label: "Optional display label"
        css_classes: [] # extra CSS classes

      - kind: spacer
        at: {row: 1, col: 2}
        span: {rows: 2, cols: 1}
```

`key` is required for port-like kinds. For `spacer` and `blank`, it is auto-generated if omitted.

**Kind values:**

| YAML value | Meaning |
|---|---|
| `port` | Front or rear port |
| `interface` | Network interface |
| `console-port` | Console port |
| `power-port` | Power port |
| `module-slot` | Module / expansion slot |
| `spacer` | Visual gap (rendered as empty cell) |
| `blank` | Decorative filler cell |
| `label` | Text label |

---

## Variant definition

Variants define an alternative layout applied when a specific module model is installed.

```yaml
variants:
  "C9300-NM-8X":          # must match the NetBox module model name exactly
    match: module          # always "module" for now
    view: front            # which view this variant applies to (default: front)
    rows:                  # full row definition for this variant
      - ...
```

The variant is a *full replacement* of the base view for that face — not a delta. To keep the base ports, re-specify them alongside the new module ports.

---

## Patch panels

Patch panels have distinct front and rear views. Use `cell_size: 0` on the canvas to enable auto sizing, and `pattern: all` for single-row sequences.

```yaml
version: 1
meta:
  description: "Generic 24-port UTP Patch Panel"
canvas:
  columns: 28
  rows: 1
  cell_size: 0      # enables patch-panel auto sizing

views:
  front:
    rows:
      - group:
          spacer: 1
          sections:
            - sequence:
                kind: port
                prefix: "port-"
                start: 1
                count: 6
                pattern: all
            - sequence:
                kind: port
                prefix: "port-"
                start: 7
                count: 6
                pattern: all

  rear:
    copy_from: front    # identical layout
```

---

## `copy_from`

A view can inherit another view's element list as a starting point:

```yaml
views:
  front:
    rows: [...]
  rear:
    copy_from: front    # starts with front's elements
```

---

## Full example — Cisco C9300-24T with expansion module variant

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

---

## Migrating from CSS to YAML

If you have existing DeviceViews with CSS `grid_template_area` layouts, run the bundled management command to convert them automatically:

```bash
cd /opt/netbox/netbox
python manage.py css_to_yaml
```

This converts every DeviceView that has a CSS layout but no YAML layout, writes the result into the **YAML Layout** field, and leaves the original CSS untouched. After running it, set **Render Mode → SVG** on the DeviceViews you want to upgrade.

---

## Ready-made examples

Example YAML files are available in [`examples/yaml/`](https://github.com/peterbaumert/netbox-device-view/tree/main/examples/yaml):

| File | Device |
|---|---|
| `Cisco/C9300-24T.yaml` | Cisco Catalyst 9300-24T (+ C9300-NM-8X variant) |
| `Cisco/C2960X-24TD-L.yaml` | Cisco Catalyst 2960X-24TD-L |
| `Cisco/C8300-2N2S-4T2X.yaml` | Cisco C8300-2N2S-4T2X |
| `Cisco/FPR1120-NGFW-K9.yaml` | Cisco Firepower FPR1120 |
| `Generic/24-ports-UTP-Patchpanel.yaml` | Generic 24-port UTP patch panel |
| `Generic/48-ports-UTP-Patchpanel.yaml` | Generic 48-port UTP patch panel |
| `Generic/24xLC-Patchpanel.yaml` | Generic 24xLC fiber patch panel |
| `Generic/SC-24-port_Fiber_Patch_Panel.yaml` | Generic SC 24-port fiber patch panel |
| `Generic/LC-48-port-Fiber-Patchpanel.yaml` | Generic LC 48-port fiber patch panel |
| `Ubiquiti/USW-Enterprise-24-PoE.yaml` | Ubiquiti USW-Enterprise-24-PoE |
