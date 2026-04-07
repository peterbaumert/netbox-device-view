"""
Management command: css_to_yaml

Reads the legacy CSS ``grid_template_area`` field from DeviceView records and
converts each CSS layout to the YAML flat-elements format, writing the result
back to the ``yaml_layout`` field.

Usage
-----
    python manage.py css_to_yaml
    python manage.py css_to_yaml --device-type "C9300-24T"
    python manage.py css_to_yaml --force      # overwrite existing yaml_layout
    python manage.py css_to_yaml --dry-run    # print YAML without saving

Algorithm
---------
1. Parse CSS with regex:
   - ``.deviceview.area`` / ``.deviceview``         → front view (base)
   - ``.deviceview.area.dFront``                    → front view (patch panel)
   - ``.deviceview.area.dRear``                     → rear view (patch panel)
   - ``.deviceview.moduleXXX.area``                 → variant block

2. For each block extract the quoted row strings from ``grid-template-areas``.

3. Build a 2D token grid; infer canvas dimensions from the largest grid.

4. Classify each token (blank / spacer / interface / console-port / port).

5. Emit a flat ``elements:`` list with explicit ``at:`` and ``span:``
   coordinates.  Tokens that span both rows of a 2-row grid are emitted
   as a single element with ``span: {rows: 2, cols: 1}``.
   Rear-view ports are spread across the full canvas width via ``col_span``.

6. Dump to YAML and (optionally) write back to the database.
"""

from __future__ import annotations

import re
import textwrap
from typing import Optional

import yaml
from django.core.management.base import BaseCommand, CommandError

from netbox_device_view.models import DeviceView

# ── CSS parsing ───────────────────────────────────────────────────────────────

# Matches one quoted row string inside grid-template-areas
_ROW_RE = re.compile(r'"([^"]+)"')

# Selector patterns — order matters (most-specific first)
_SEL_VARIANT = re.compile(
    r"\.deviceview\.module([^.\s{]+)(?:\.area)?\s*\{([^}]*)\}", re.DOTALL
)
_SEL_DFRONT = re.compile(r"\.deviceview(?:\.area)?\.dFront\s*\{([^}]*)\}", re.DOTALL)
_SEL_DREAR = re.compile(r"\.deviceview(?:\.area)?\.dRear\s*\{([^}]*)\}", re.DOTALL)
_SEL_BASE = re.compile(r"\.deviceview(?:\.area)?\s*\{([^}]*)\}", re.DOTALL)

# ── Token classification ───────────────────────────────────────────────────────

_BLANK_TOKENS = {"x", "y", "z"}
_SPACER_RE = re.compile(r"^s\d+$")
_BLANK_RE = re.compile(r"^[xyz]\d*$")  # x, y, z with optional trailing digits
_CONSOLE_RE = re.compile(r"^con[\d-]")  # con0, con-0, con1, etc.
_INTERFACE_PREFIXES = (
    "gigabitethernet",
    "tengigabitethernet",
    "fastethernet",
    "twentyfivegige",
    "hundredgige",
    "fortygige",
    "twohundredfiftygige",
    "sfp",
    "eth",
    "mgmt",
)
_CONSOLE_PREFIXES = ("console",)


def _classify(token: str) -> str:
    """Return the YAML ``kind`` string for a CSS token."""
    t = token.lower()
    if _BLANK_RE.match(t):
        return "blank"
    if _SPACER_RE.match(t):
        return "spacer"
    if _CONSOLE_RE.match(t):
        return "console-port"
    for pfx in _CONSOLE_PREFIXES:
        if t.startswith(pfx):
            return "console-port"
    for pfx in _INTERFACE_PREFIXES:
        if t.startswith(pfx):
            return "interface"
    return "port"


# ── CSS block → 2D grid ───────────────────────────────────────────────────────


def _parse_rows(css_block: str) -> list[list[str]]:
    """
    Extract the 2D token grid from the body of a CSS rule block.

    Returns a list of rows, each a list of token strings.
    Returns an empty list if no ``grid-template-areas`` rows are found.
    """
    rows = []
    for m in _ROW_RE.finditer(css_block):
        tokens = m.group(1).split()
        if tokens:
            rows.append(tokens)
    return rows


# ── Grid → elements ───────────────────────────────────────────────────────────


def _grid_to_elements(
    grid: list[list[str]],
    canvas_rows: int,
    canvas_cols: int,
    col_span_override: Optional[int] = None,
) -> list[dict]:
    """
    Convert a 2D token grid to a flat list of element dicts.

    Tokens that appear identically in every row of a multi-row grid at the
    same column position are emitted as a single element spanning all rows.
    Otherwise each cell is emitted individually.

    ``col_span_override`` forces every port element to use that col_span
    (used for rear views with few ports spread across the full canvas width).
    """
    if not grid:
        return []

    num_rows = len(grid)
    # Pad shorter rows to canvas_cols
    padded = []
    for row in grid:
        padded.append(row + ["x"] * max(0, canvas_cols - len(row)))

    elements: list[dict] = []
    seen: set[tuple[int, int]] = set()  # (row_idx, col_idx) cells already emitted

    for col_idx in range(canvas_cols):
        for row_idx in range(num_rows):
            if (row_idx, col_idx) in seen:
                continue

            token = padded[row_idx][col_idx] if col_idx < len(padded[row_idx]) else "x"
            kind = _classify(token)

            # Check if this token spans all remaining rows at this column
            span_rows = 1
            if num_rows > 1 and row_idx == 0:
                # Does this token appear in every lower row at the same column?
                if all(
                    col_idx < len(padded[r]) and padded[r][col_idx] == token
                    for r in range(1, num_rows)
                ):
                    span_rows = num_rows
                    for r in range(num_rows):
                        seen.add((r, col_idx))
                else:
                    seen.add((row_idx, col_idx))
            else:
                seen.add((row_idx, col_idx))

            at = {"row": row_idx + 1, "col": col_idx + 1}
            span: dict = {}
            if span_rows > 1:
                span["rows"] = span_rows
            eff_col_span = (
                col_span_override
                if (col_span_override and col_span_override > 1)
                else 1
            )
            if eff_col_span > 1:
                span["cols"] = eff_col_span

            el: dict = {"kind": kind, "at": at}
            if span:
                el["span"] = span

            # Port-like elements need a key
            if kind not in ("blank", "spacer"):
                el["key"] = token
            # Spacers/blanks: omit key (auto-generated by parser)

            elements.append(el)

    return elements


def _grid_to_elements_spanned(
    grid: list[list[str]],
    num_rows: int,
    num_cols: int,
    col_span: int,
) -> list[dict]:
    """
    Convert a grid where each cell should be rendered ``col_span`` columns wide.

    Used for rear views with few ports that need to fill the full canvas width.
    Column placement is computed as ``col_idx * col_span + 1`` so each port
    starts immediately after the previous one's span ends.
    """
    if not grid:
        return []

    elements: list[dict] = []
    for col_idx in range(num_cols):
        token = grid[0][col_idx] if col_idx < len(grid[0]) else "x"
        kind = _classify(token)
        placed_col = col_idx * col_span + 1

        el: dict = {
            "kind": kind,
            "at": {"row": 1, "col": placed_col},
        }
        span: dict = {}
        if num_rows > 1:
            span["rows"] = num_rows
        if col_span > 1:
            span["cols"] = col_span
        if span:
            el["span"] = span
        if kind not in ("blank", "spacer"):
            el["key"] = token

        elements.append(el)

    return elements


# ── Main conversion ───────────────────────────────────────────────────────────


def _css_to_yaml_dict(css: str) -> dict:
    """
    Parse a full CSS ``grid_template_area`` string and return a YAML-ready dict.
    """
    # ── Step 1: extract all blocks ────────────────────────────────────────────

    # Variants (must come before base so we can strip them before matching base)
    variants_raw: dict[str, list[list[str]]] = {}
    for m in _SEL_VARIANT.finditer(css):
        variant_name = m.group(1)
        grid = _parse_rows(m.group(2))
        if grid:
            variants_raw[variant_name] = grid

    # Strip variant blocks so they don't confuse the base selectors
    css_stripped = _SEL_VARIANT.sub("", css)

    # Front / rear (dFront / dRear)
    dfront_grid: list[list[str]] = []
    drear_grid: list[list[str]] = []
    m_df = _SEL_DFRONT.search(css_stripped)
    if m_df:
        dfront_grid = _parse_rows(m_df.group(1))
    m_dr = _SEL_DREAR.search(css_stripped)
    if m_dr:
        drear_grid = _parse_rows(m_dr.group(1))

    # Base (no dFront/dRear suffix)
    base_grid: list[list[str]] = []
    if not dfront_grid:
        m_base = _SEL_BASE.search(css_stripped)
        if m_base:
            base_grid = _parse_rows(m_base.group(1))

    # Decide front grid
    front_grid = dfront_grid or base_grid

    # ── Step 2: determine canvas dimensions ───────────────────────────────────

    all_grids = [front_grid, drear_grid] + list(variants_raw.values())
    canvas_cols = max(
        (max(len(row) for row in g) for g in all_grids if g),
        default=32,
    )
    canvas_rows = len(front_grid) if front_grid else 1

    # ── Step 3: build views ───────────────────────────────────────────────────

    views: dict = {}

    if front_grid:
        front_elements = _grid_to_elements(front_grid, canvas_rows, canvas_cols)
        views["front"] = {"elements": front_elements}

    if drear_grid:
        rear_num_rows = len(drear_grid)
        rear_port_count = max(len(row) for row in drear_grid)
        # Spread rear ports across the full canvas width using col_span.
        # col placement must account for the wider span so ports don't overlap.
        col_span = max(1, canvas_cols // rear_port_count) if rear_port_count else 1
        rear_elements = _grid_to_elements_spanned(
            drear_grid, rear_num_rows, rear_port_count, col_span
        )
        views["rear"] = {"elements": rear_elements}

    # ── Step 4: build variants ────────────────────────────────────────────────

    variants: dict = {}
    for variant_name, grid in variants_raw.items():
        v_rows = len(grid)
        v_elements = _grid_to_elements(grid, v_rows, canvas_cols)
        variants[variant_name] = {"elements": v_elements}

    # ── Step 5: assemble top-level dict ──────────────────────────────────────

    layout: dict = {
        "version": 1,
        "canvas": {
            "columns": canvas_cols,
            "rows": canvas_rows,
        },
    }
    if views:
        layout["views"] = views
    if variants:
        layout["variants"] = variants

    return layout


def _dump_yaml(data: dict) -> str:
    """Dump the layout dict to a compact-ish YAML string."""
    return yaml.dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )


# ── Django management command ─────────────────────────────────────────────────


class Command(BaseCommand):
    help = (
        "Convert legacy CSS grid_template_area layouts to YAML and write "
        "them back to the yaml_layout field on DeviceView records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--device-type",
            metavar="MODEL",
            help="Only process the DeviceView for this device type model name.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Overwrite existing yaml_layout values.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Print the generated YAML without writing to the database.",
        )

    def handle(self, *args, **options):
        device_type = options["device_type"]
        force = options["force"]
        dry_run = options["dry_run"]

        qs = DeviceView.objects.select_related("device_type").all()
        if device_type:
            qs = qs.filter(device_type__model=device_type)
            if not qs.exists():
                raise CommandError(
                    f"No DeviceView found for device type model: {device_type!r}"
                )

        if not force:
            qs = qs.filter(yaml_layout="")

        if not qs.exists():
            self.stdout.write(
                self.style.WARNING(
                    "No DeviceViews to process "
                    "(all already have yaml_layout; use --force to overwrite)."
                )
            )
            return

        converted = 0
        errors = 0

        for dv in qs:
            model_name = dv.device_type.model
            self.stdout.write(f"Processing: {model_name}")

            try:
                layout_dict = _css_to_yaml_dict(dv.grid_template_area)
                yaml_text = _dump_yaml(layout_dict)
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  ERROR parsing CSS for {model_name!r}: {exc}")
                )
                errors += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(f"  --- YAML for {model_name} ---")
                )
                self.stdout.write(textwrap.indent(yaml_text, "  "))
            else:
                dv.yaml_layout = yaml_text
                dv.save(update_fields=["yaml_layout"])
                self.stdout.write(
                    self.style.SUCCESS(f"  Saved yaml_layout ({len(yaml_text)} chars)")
                )

            converted += 1

        self.stdout.write("")
        summary = f"Done: {converted} converted"
        if errors:
            summary += f", {errors} errors"
        if dry_run:
            summary += " (dry-run, nothing written)"
        self.stdout.write(self.style.SUCCESS(summary))
