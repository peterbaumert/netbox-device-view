# YAML Layout Schema Reference

This document describes the full YAML schema for `netbox-device-view` layouts.

## Top-level structure

```yaml
version: 1          # required — must be 1

meta:               # optional
  description: "Human-readable name"
  background: "#d9d9d9"   # default background for all views

canvas:             # optional — defaults shown
  columns: 32       # total grid columns
  rows: 2           # total grid rows
  cell_size: 20     # px per cell (reserved for future SVG renderer)
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
|---------|-----------|
| `top-odd`   | Odd-numbered ports → row 1 (top); even-numbered ports → row 2 (standard 2U Cisco style) |
| `top-even`  | Even-numbered ports → row 1 (top); odd-numbered ports → row 2 (inverted) |
| `all`   | All ports in a single row (patch-panel style); use `row: 2` to place them in the bottom row |

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

Spacers use auto-generated keys (`s1`, `s2`, …). They appear as empty visual gaps.

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
        face: both      # front | rear | both  (default: both)
        css_classes: [] # extra CSS classes

      - kind: spacer
        at: {row: 1, col: 2}
        span: {rows: 2, cols: 1}
```

`key` is required for port-like kinds. For `spacer` and `blank`, it is auto-generated if omitted.

**Kind values:**

| YAML value | Meaning |
|------------|---------|
| `port` | Front or rear port |
| `interface` | Network interface |
| `console-port` | Console port |
| `power-port` | Power port |
| `module-slot` | Module / expansion slot |
| `spacer` | Visual gap (rendered as empty cell) |
| `blank` | Decorative filler cell |
| `label` | Text label (future use) |

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
    elements:              # OR flat elements
      - ...
```

The rendered CSS for a variant is a *full replacement* of the base view's grid (not a delta). To include the base ports, re-specify them in the variant's `rows:` block alongside the new module ports.

---

## Patch panels

Patch panels have distinct front and rear views. Use `cell_size: 0` on the canvas to enable `grid-auto-rows: auto; grid-auto-columns: auto` sizing, and `pattern: all` for single-row sequences.

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
            # ... more sections

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
    # additional elements here override by key
```

---

## CSS output conventions

The YAML renderer produces CSS equivalent to the legacy hand-written format:

| Scenario | CSS selector |
|----------|-------------|
| Single-face device (switch, router) | `.deviceview.area` |
| Patch panel front | `.deviceview.area.dFront` |
| Patch panel rear | `.deviceview.area.dRear` |
| Module variant | `.deviceview.module{ModelName}.area` |

---

## Examples

Ready-made YAML layouts are in [`examples/yaml/`](../examples/yaml/):

| File | Device |
|------|--------|
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
