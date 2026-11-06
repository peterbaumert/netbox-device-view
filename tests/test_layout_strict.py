"""
Tests for the strict parser rules that the Data Source import applies.

These are pure unit tests: no Django, no DB, no NetBox required.
"""

import copy
import time
from pathlib import Path

import pytest
import yaml

from netbox_device_view.layout import Face, render_css
from netbox_device_view.layout.parser import (
    MAX_CANVAS_CELLS,
    MAX_CANVAS_ROWS,
    MAX_CSS_CLASSES,
    MAX_EXPANDED_ELEMENTS,
    MAX_KEY_LENGTH,
    MAX_PAINTED_CELLS,
    MAX_RENDERED_CELLS,
    LayoutParseError,
    parse,
)

EXAMPLES = sorted(
    (Path(__file__).resolve().parents[1] / "examples" / "yaml").glob("*/*.yaml")
)

BASE = {
    "version": 1,
    "meta": {"description": "Test", "background": "#d9d9d9"},
    "canvas": {"columns": 8, "rows": 2, "cell_size": 20},
    "views": {
        "front": {
            "rows": [
                {"blank": 1},
                {"sequence": {"kind": "interface", "prefix": "eth", "count": 4}},
            ],
        }
    },
    "variants": {
        "NM-2X": {
            "match": "module",
            "view": "front",
            "elements": [
                {
                    "kind": "port",
                    "key": "te1",
                    "at": {"row": 1, "col": 8},
                    "span": {"rows": 2, "cols": 1},
                    "label": "TE 1",
                    "css_classes": ["uplink", "_x-1"],
                    "face": "front",
                }
            ],
        }
    },
}


def strict(data):
    text = data if isinstance(data, str) else yaml.safe_dump(data)
    return parse(text, strict=True)


def layout(change):
    data = copy.deepcopy(BASE)
    change(data)
    return data


def front(data):
    return data["views"]["front"]


def variant_element(data):
    return data["variants"]["NM-2X"]["elements"][0]


def separate_faces(data):
    front(data)["elements"] = [{"kind": "port", "key": "p1"}]
    del front(data)["rows"]
    data["views"]["rear"] = {"copy_from": "front"}


def test_base_layout_passes():
    parsed = strict(BASE)
    assert [el.key for el in parsed.front.elements_for_variant("NM-2X")][-1] == "te1"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.stem)
def test_bundled_examples_pass(example):
    strict(example.read_text())


def test_lenient_parse_is_unchanged():
    assert parse("views: {}").views == {}
    with pytest.raises(LayoutParseError, match="grid area"):
        strict("views: {}")


class TestLimits:
    def test_canvas_cell_limit(self):
        strict(layout(lambda d: d["canvas"].update(columns=MAX_CANVAS_CELLS // 2)))
        with pytest.raises(LayoutParseError, match=f"{MAX_CANVAS_CELLS} cells"):
            strict(
                layout(lambda d: d["canvas"].update(columns=MAX_CANVAS_CELLS // 2 + 1))
            )

    def test_view_canvas_cell_limit(self):
        with pytest.raises(LayoutParseError, match=f"{MAX_CANVAS_CELLS} cells"):
            strict(
                layout(lambda d: front(d).update(canvas={"columns": 4096, "rows": 2}))
            )

    @pytest.mark.parametrize(
        "row",
        [
            {"sequence": {"prefix": "p", "count": 10**12}},
            {"blank": 10**12},
            {"spacer": 10**12},
            {"group": {"spacer": 10**12, "sections": [{"blank": 1}, {"blank": 1}]}},
        ],
    )
    def test_expansion_is_checked_before_allocation(self, row):
        started = time.monotonic()
        with pytest.raises(LayoutParseError, match=f"{MAX_EXPANDED_ELEMENTS} elements"):
            strict(layout(lambda d: front(d)["rows"].append(row)))
        assert time.monotonic() - started < 5

    def test_expansion_budget_spans_views_and_variants(self):
        # Each part fits a canvas; together they exceed the budget.
        part = MAX_EXPANDED_ELEMENTS // 4 + 1
        canvas = {"columns": part, "rows": 1, "cell_size": 0}
        sequence = {"sequence": {"prefix": "p", "count": part, "pattern": "all"}}

        def change(d):
            d["canvas"] = canvas
            d["views"] = {"front": {"rows": [sequence]}}
            d["variants"] = {name: {"rows": [sequence]} for name in ("A", "B", "C")}

        with pytest.raises(LayoutParseError, match=f"{MAX_EXPANDED_ELEMENTS} elements"):
            strict(layout(change))


class TestCycles:
    def test_recursive_alias(self):
        with pytest.raises(LayoutParseError, match="Recursive YAML alias"):
            strict("meta: &m {description: x, background: *m}\nviews: {}")

    def test_shared_aliases_are_walked_once(self):
        levels = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
        levels += [
            f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 10)}]" for i in range(1, 12)
        ]
        text = "meta:\n  description:\n    " + "\n    ".join(levels) + "\nviews: {}"
        started = time.monotonic()
        with pytest.raises(LayoutParseError, match="meta.description"):
            strict(text)
        assert time.monotonic() - started < 5

    def test_deep_nesting(self):
        with pytest.raises(LayoutParseError, match="nested too deeply"):
            strict("meta: " + "[" * 100000)

    @pytest.mark.parametrize(
        "views",
        [
            {"front": {"copy_from": "front"}},
            {"front": {"copy_from": "rear"}, "rear": {"copy_from": "front"}},
        ],
    )
    def test_cyclic_or_chained_copy_from(self, views):
        with pytest.raises(LayoutParseError, match="copy_from"):
            strict(layout(lambda d: d.update(views=views)))


INJECTION = 'x" onmouseover="alert(1)'


class TestCssClasses:
    @pytest.mark.parametrize(
        "value",
        [
            "uplink",
            "a b",
            [""],
            ["a b"],
            [INJECTION],
            ["x'"],
            ["a\\62"],
            [1],
            [None],
            ["café"],
            ["1a"],
            ["-a"],
            ["a\t"],
        ],
    )
    def test_rejected_in_variant(self, value):
        with pytest.raises(LayoutParseError, match="css_classes"):
            strict(layout(lambda d: variant_element(d).update(css_classes=value)))

    def test_rejected_in_base_element(self):
        def change(d):
            front(d)["elements"] = [{"key": "p1", "css_classes": [INJECTION]}]
            del front(d)["rows"]

        with pytest.raises(LayoutParseError, match="css_classes"):
            strict(layout(change))


class TestFields:
    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda d: variant_element(d).update(key="te 1"), "key"),
            (lambda d: variant_element(d).update(key='te"1'), "key"),
            (lambda d: variant_element(d).update(key="1"), "key"),
            (lambda d: variant_element(d).update(key=7), "key"),
            (lambda d: front(d)["rows"][1]["sequence"].update(prefix="eth "), "key"),
            (lambda d: front(d)["rows"][1]["sequence"].update(prefix=""), "key"),
            (lambda d: front(d)["rows"].append({"elements": ["a}b"]}), "key"),
            (lambda d: d["variants"].update({"bad name": {"elements": []}}), "variant"),
            (lambda d: d["variants"].update({'x"}': {"elements": []}}), "variant"),
            (lambda d: variant_element(d).update(label=5), "label"),
            (lambda d: variant_element(d).update(label="a\nb"), "label"),
            (lambda d: variant_element(d).update(label="x" * 65), "label"),
            (lambda d: d["meta"].update(background="red; } body {"), "background"),
            (lambda d: d["meta"].update(background="#12"), "background"),
            (lambda d: d["canvas"].update(background=5), "background"),
            (lambda d: front(d).update(background="url(x)"), "background"),
            (lambda d: d["meta"].update(background="notacolor"), "background"),
            (lambda d: d["meta"].update(background="red /*"), "background"),
            (lambda d: d["meta"].update(background="/**/red"), "background"),
            (lambda d: d["meta"].update(background="red blue"), "background"),
            (lambda d: d["meta"].update(description=["x"]), "description"),
            (lambda d: d["canvas"].update(columns=0), "columns"),
            (lambda d: d["canvas"].update(columns=True), "columns"),
            (lambda d: d["canvas"].update(columns="8"), "columns"),
            (lambda d: d["canvas"].update(rows=-1), "rows"),
            (lambda d: d["canvas"].update(cell_size=-1), "cell_size"),
            (lambda d: d["canvas"].update(cell_size=101), "cell_size"),
            (lambda d: d.update(version="1"), "version"),
            (lambda d: d.update(version=2), "version"),
            (lambda d: variant_element(d).update(kind="flux"), "kind"),
            (lambda d: variant_element(d).update(face="side"), "face"),
            (lambda d: d["views"].update(side=d["views"].pop("front")), "side"),
            (lambda d: d["variants"]["NM-2X"].update(match="slot"), "match"),
            (lambda d: d["variants"]["NM-2X"].update(view="side"), "view"),
            (
                lambda d: front(d)["rows"][1]["sequence"].update(pattern="zig"),
                "pattern",
            ),
            (lambda d: front(d)["rows"][1]["sequence"].update(start="a"), "start"),
            (lambda d: front(d)["rows"][1]["sequence"].update(count=-1), "count"),
            (lambda d: front(d)["rows"][1]["sequence"].update(step=0), "step"),
            (lambda d: front(d)["rows"][1]["sequence"].update(row=3), "row"),
            (lambda d: front(d)["rows"].append({"spacer": -1}), "spacer"),
            (lambda d: front(d)["rows"].append({"blank": "2"}), "blank"),
            (lambda d: variant_element(d).update(at={"row": 0, "col": 1}), "row"),
            (lambda d: variant_element(d).update(span={"rows": 1, "cols": 0}), "cols"),
            (lambda d: front(d).update(rows="blank"), "rows"),
            (
                lambda d: front(d)["rows"].append({"spacer": 1, "blank": 1}),
                "exactly one",
            ),
            (lambda d: front(d)["rows"].append({"extra": 1}), "exactly one"),
            (
                lambda d: front(d)["rows"].append({"group": {"sections": [{"x": 1}]}}),
                "exactly one",
            ),
            (lambda d: variant_element(d).update(css_classes=None), "css_classes"),
        ],
    )
    def test_rejected(self, change, message):
        with pytest.raises(LayoutParseError, match=message):
            strict(layout(change))

    @pytest.mark.parametrize(
        ("background", "stored"),
        [
            ("black", "black"),
            ("#abc", "#abc"),
            ("#aabbccdd", "#aabbccdd"),
            ("transparent", "transparent"),
            ("rgb(0 0 0 / 50%)", "rgb(0 0 0 / 50%)"),
            ("lab(50% 40 59)", "lab(50% 40 59)"),
            # CSS closes a function at the end of input; the stored form does too.
            ("rgb(0, 0, 0", "rgb(0, 0, 0)"),
            ("  black  ", "black"),
        ],
    )
    def test_css_colors_are_accepted_in_serialized_form(self, background, stored):
        parsed = strict(layout(lambda d: d["meta"].update(background=background)))
        assert parsed.front.canvas.background == stored

    def test_view_background_is_applied(self):
        parsed = parse(
            yaml.safe_dump(layout(lambda d: front(d).update(background="#000")))
        )
        assert parsed.front.canvas.background == "#000"


class TestUnknownKeys:
    @pytest.mark.parametrize(
        "change",
        [
            lambda d: d.update(extra=1),
            lambda d: d["meta"].update(extra=1),
            lambda d: d["canvas"].update(extra=1),
            lambda d: front(d).update(extra=1),
            lambda d: d["variants"]["NM-2X"].update(extra=1),
            lambda d: front(d)["rows"][1]["sequence"].update(pattren="all"),
            lambda d: front(d)["rows"].append({"group": {"sections": [], "extra": 1}}),
            lambda d: variant_element(d).update(css_class=["a"]),
            lambda d: variant_element(d).update(at={"row": 1, "col": 1, "x": 1}),
            lambda d: variant_element(d).update(span={"rows": 1, "colz": 1}),
        ],
    )
    def test_rejected(self, change):
        with pytest.raises(LayoutParseError, match="unknown key"):
            strict(layout(change))


class TestGridAreas:
    @pytest.mark.parametrize(
        "change",
        [
            lambda d: d.update(views={}, variants={}),
            lambda d: d.update(views={"front": {}}),
            lambda d: d.pop("views") and d.pop("variants"),
        ],
    )
    def test_at_least_one_element_is_required(self, change):
        with pytest.raises(LayoutParseError, match="grid area"):
            strict(layout(change))

    @pytest.mark.parametrize(
        ("change", "where", "key"),
        [
            (
                lambda d: front(d)["rows"][1]["sequence"].update(
                    count=8, pattern="all"
                ),
                "views.front",
                "eth8",
            ),
            (
                lambda d: variant_element(d).update(at={"row": 1, "col": 9}),
                "variants.NM-2X",
                "te1",
            ),
            (
                lambda d: variant_element(d).update(span={"rows": 3, "cols": 1}),
                "variants.NM-2X",
                "te1",
            ),
            (
                lambda d: d["views"].update(
                    rear={
                        "canvas": {"rows": 1},
                        "elements": [{"key": "p1", "at": {"row": 2}}],
                    }
                )
                or d.pop("variants"),
                "views.rear",
                "p1",
            ),
        ],
    )
    def test_elements_outside_the_canvas_are_rejected(self, change, where, key):
        with pytest.raises(LayoutParseError) as excinfo:
            strict(layout(change))
        message = str(excinfo.value)
        assert message.startswith(f"{where}:")
        assert f"element {key!r}" in message
        assert "outside the canvas of view" in message

    def test_element_on_the_canvas_edge_is_accepted(self):
        def change(d):
            variant_element(d).update(
                at={"row": 2, "col": 8}, span={"rows": 1, "cols": 1}
            )

        strict(layout(change))

    def test_lenient_parse_accepts_elements_outside_the_canvas(self):
        data = layout(lambda d: variant_element(d).update(at={"row": 1, "col": 9}))
        parsed = parse(yaml.safe_dump(data))
        assert parsed.front.elements_for_variant("NM-2X")[-1].col == 9

    def test_port_names_need_not_match_components(self):
        strict(layout(lambda d: variant_element(d).update(key="does-not-exist")))


def rear_only(data):
    data["views"] = {"rear": data["views"].pop("front")}


class TestVariantViews:
    """Strict layouts accept only the variants that the CSS renderer writes."""

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (
                separate_faces,
                "variants: a layout with both a front and a rear view cannot have variants",
            ),
            (
                lambda d: d["variants"]["NM-2X"].update(view="rear"),
                "variants.NM-2X.view: the layout has no rear view",
            ),
            (rear_only, "variants.NM-2X.view: the layout has no front view"),
            (
                lambda d: d.update(views={}),
                "variants.NM-2X.view: the layout has no front view",
            ),
        ],
    )
    def test_variants_that_do_not_render_are_rejected(self, change, message):
        with pytest.raises(LayoutParseError, match=message):
            strict(layout(change))

    @pytest.mark.parametrize(
        "change",
        [
            lambda d: None,
            lambda d: (rear_only(d), d["variants"]["NM-2X"].update(view="rear")),
        ],
    )
    def test_accepted_variants_are_rendered(self, change):
        assert ".deviceview.moduleNM-2X.area {" in render_css(strict(layout(change)))

    @pytest.mark.parametrize("views", [{}, {"front": {"elements": [{"key": "p1"}]}}])
    def test_lenient_parse_adds_the_view_of_a_variant_with_its_face(self, views):
        data = {"views": views, "variants": {"V": {"view": "rear", "elements": []}}}
        parsed = parse(yaml.safe_dump(data))
        assert parsed.rear.face == Face.REAR
        assert "V" in parsed.rear.variants


def flat(canvas, front_elements, variant_elements=None):
    """Return a front-only layout with flat elements and an optional variant V."""
    data = {
        "version": 1,
        "canvas": {"columns": canvas[0], "rows": canvas[1]},
        "views": {"front": {"elements": front_elements}},
    }
    if variant_elements is not None:
        data["variants"] = {"V": {"elements": variant_elements}}
    return data


BIG = {"key": "big", "at": {"row": 1, "col": 1}, "span": {"rows": 2, "cols": 2}}
SMALL = {"key": "small", "at": {"row": 2, "col": 2}}


class TestAreaShapes:
    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (
                flat((4, 1), [{"key": "p1"}, {"key": "p1", "at": {"col": 3}}]),
                "views.front: the cells of grid area 'p1'",
            ),
            (
                flat((4, 2), [{**BIG, "kind": "blank"}, SMALL]),
                "views.front: the cells of grid area 'big'",
            ),
        ],
    )
    def test_areas_that_are_not_rectangles_are_rejected(self, data, message):
        with pytest.raises(LayoutParseError, match=message):
            strict(data)

    @pytest.mark.parametrize(
        "data",
        [
            flat((4, 2), [BIG, {"key": "p1", "at": {"row": 1, "col": 3}}]),
            flat((4, 1), [{"key": "p1"}, {"key": "p1", "at": {"col": 2}}]),
            flat((4, 2), [BIG], [{**BIG, "key": "big", "span": {"rows": 1}}]),
            flat((4, 2), [SMALL], [{"key": "small", "at": {"row": 1, "col": 1}}]),
            flat(
                (4, 2),
                [
                    {**BIG, "kind": "spacer"},
                    {"key": "p1", "at": {"col": 2}, "span": {"rows": 2}},
                ],
            ),
            flat((4, 1), [{"key": "p1"}, {"key": "p1", "at": {"col": 1}}]),
        ],
    )
    def test_rectangular_areas_are_accepted(self, data):
        strict(data)

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (
                flat((4, 1), [{"key": "p1"}, {"key": "p2", "at": {"col": 1}}]),
                "views.front: element 'p2' covers port 'p1'",
            ),
            (
                flat((4, 2), [{"key": "p2", "at": {"row": 1, "col": 1}}, BIG]),
                "views.front: element 'big' covers port 'p2'",
            ),
            (
                flat((4, 2), [BIG, SMALL]),
                "views.front: element 'small' covers port 'big'",
            ),
            (
                flat((4, 2), [BIG], [SMALL]),
                "variants.V: element 'small' covers port 'big'",
            ),
            (
                flat((4, 2), [SMALL], [BIG]),
                "variants.V: element 'small' covers port 'big'",
            ),
            (
                flat((4, 1), [{"key": "p1"}, {"kind": "blank", "key": "x1"}]),
                "views.front: element 'x1' covers port 'p1'",
            ),
        ],
    )
    def test_elements_over_a_port_are_rejected(self, data, message):
        # A port that loses all its cells leaves the area shapes valid.
        with pytest.raises(LayoutParseError, match=message):
            strict(data)

    def test_a_view_copy_keeps_repeated_keys(self):
        data = flat((4, 1), [{"key": "p1"}, {"key": "p1", "at": {"col": 2}}])
        data["views"]["rear"] = {"copy_from": "front"}

        assert '"p1 p1 . ."' in render_css(strict(data)).split(".dRear")[1]

    def test_lenient_parse_accepts_overlaps(self):
        assert (
            len(parse(yaml.safe_dump(flat((4, 2), [BIG, SMALL]))).front.elements) == 2
        )


class TestReviewFindings:
    """Inputs that once passed strict parse with unbounded cost, or failed with a non-parse error."""

    def test_long_sequence_prefix_is_rejected(self):
        sequence = {"prefix": "a" * (MAX_KEY_LENGTH + 1), "count": 2}
        with pytest.raises(LayoutParseError, match="prefix"):
            strict(layout(lambda d: front(d)["rows"].append({"sequence": sequence})))

    def test_long_element_key_is_rejected(self):
        with pytest.raises(LayoutParseError, match="key"):
            strict(layout(lambda d: variant_element(d).update(key="a" * 65)))

    def test_too_many_css_classes_are_rejected(self):
        classes = [f"c{i}" for i in range(MAX_CSS_CLASSES + 1)]
        with pytest.raises(LayoutParseError, match="css_classes"):
            strict(layout(lambda d: variant_element(d).update(css_classes=classes)))

    def test_rendered_cells_are_bounded_across_variants(self):
        # Each variant renders the whole canvas again, even with no elements.
        def change(d):
            d["canvas"] = {"columns": 2048, "rows": 2, "cell_size": 0}
            d["variants"] = {f"V{i}": {"elements": []} for i in range(40)}

        with pytest.raises(LayoutParseError, match=f"{MAX_RENDERED_CELLS} cells"):
            strict(layout(change))

    def test_painted_cells_are_bounded(self):
        # Each element paints its whole span, so full-canvas elements multiply the cost.
        def change(d):
            d["canvas"] = {"columns": 64, "rows": MAX_CANVAS_ROWS}
            d["variants"] = {}
            del front(d)["rows"]
            front(d)["elements"] = [
                {
                    "kind": "port",
                    "key": f"p{i}",
                    "span": {"rows": MAX_CANVAS_ROWS, "cols": 64},
                }
                for i in range(MAX_PAINTED_CELLS // (64 * MAX_CANVAS_ROWS) + 1)
            ]

        with pytest.raises(LayoutParseError, match=f"{MAX_PAINTED_CELLS} grid cells"):
            strict(layout(change))

    def test_base_elements_are_charged_in_every_variant(self):
        # Each variant renders the base elements again, also those that overlap.
        def change(d):
            d["canvas"] = {"columns": 1, "rows": 1}
            del front(d)["rows"]
            front(d)["elements"] = [{"key": f"p{i}"} for i in range(1000)]
            d["variants"] = {f"V{i}": {"elements": []} for i in range(100)}

        with pytest.raises(LayoutParseError, match=f"{MAX_PAINTED_CELLS} grid cells"):
            strict(layout(change))

    def test_repeated_base_keys_are_charged_in_every_variant(self):
        # Each variant renders all base elements, also those that share a key.
        full = {"key": "p1", "span": {"rows": MAX_CANVAS_ROWS, "cols": 64}}
        data = flat((64, MAX_CANVAS_ROWS), [full] * 5)
        data["variants"] = {f"V{i}": {"elements": []} for i in range(3)}

        with pytest.raises(LayoutParseError, match=f"{MAX_PAINTED_CELLS} grid cells"):
            strict(data)

    def test_canvas_rows_are_bounded(self):
        with pytest.raises(LayoutParseError, match="rows"):
            strict(
                layout(
                    lambda d: d["canvas"].update(columns=1, rows=MAX_CANVAS_ROWS + 1)
                )
            )

    def test_entries_without_elements_use_the_budget(self):
        def change(d):
            d["canvas"] = {"columns": 64, "rows": MAX_CANVAS_ROWS}
            front(d)["rows"] = [{"spacer": 0}] * (MAX_EXPANDED_ELEMENTS + 1)

        started = time.monotonic()
        with pytest.raises(LayoutParseError, match=f"{MAX_EXPANDED_ELEMENTS} elements"):
            strict(layout(change))
        assert time.monotonic() - started < 5

    def test_deep_nesting_fails_fast(self):
        started = time.monotonic()
        with pytest.raises(LayoutParseError, match="^Invalid YAML: the document"):
            strict("meta: " + "[" * 100000)
        assert time.monotonic() - started < 5

    @pytest.mark.parametrize(
        "text",
        [
            "views:\n  front: {copy_from: rear}\n  rear: null\n",
            "views:\n  front: {copy_from: rear}\n  rear: [1]\n",
            "views:\n  front: {copy_from: [x]}\n",
            "views:\n  front: {elements: [{kind: [x], key: p1}]}\n",
            "views:\n  front: {rows: [{sequence: {kind: [x]}}]}\n",
            "views:\n  front: {elements: [{key: [p1]}]}\n",
            "views:\n  front: {elements: [{key: p1, at: {row: " + "9" * 5000 + "}}]}\n",
            "meta: 0\nviews: {front: {elements: [p1]}}\n",
            "views:\n  front: {elements: [{key: p1, at: 0}]}\n",
            "views:\n  front: {elements: [{key: p1, span: false}]}\n",
            "views:\n  front: {elements: [p1], rows: [{bogus: 1}]}\n",
            "views: {front: {elements: [p1]}}\nvariants: {V: {elements: [p1], rows: [{x: 1}]}}\n",
            "version: 1\nviews: [front]\n",
            *(f"{{{tag} bogus: 1}}\n" for tag in ("!!seq", "!!map", "!!set", "!!omap")),
        ],
    )
    def test_malformed_input_raises_a_parse_error(self, text):
        with pytest.raises(LayoutParseError):
            strict(text)

    @pytest.mark.parametrize(
        "text",
        [
            "views:\n  front: {elements: [{key: p1}]}\n  front: {elements: [{key: p2}]}\n",
            "views:\n  front:\n    elements: [{key: p1, key: p2}]\n",
            "views:\n  front: {elements: [{key: p1}]}\nviews: {}\n",
            "views:\n  front: {elements: [{<<: {key: p1, key: p2}}]}\n",
            "views:\n  front: {<<: {elements: [{key: p1}]}, <<: {canvas: {rows: 1}}}\n",
        ],
    )
    def test_duplicate_keys_are_rejected(self, text):
        with pytest.raises(LayoutParseError, match="duplicate key"):
            strict(text)

    def test_merge_keys_still_apply(self):
        text = (
            "views:\n  front:\n    elements:\n"
            "      - &p1 {key: p1, label: one}\n"
            "      - {<<: *p1, key: p2, label: two, at: {col: 2}}\n"
        )
        elements = strict(text).front.elements
        assert [(e.key, e.label) for e in elements] == [("p1", "one"), ("p2", "two")]

    def test_a_merge_key_can_list_several_mappings(self):
        text = (
            "views:\n  front:\n    elements:\n"
            "      - &p1 {key: p1, label: one}\n"
            "      - &p3 {key: p3, label: three, css_classes: [c], at: {col: 2}}\n"
            "      - {<<: [*p1, *p3], key: p2, at: {col: 3}}\n"
        )
        element = strict(text).front.elements[-1]
        assert (element.key, element.label, element.css_classes) == ("p2", "one", ["c"])

    def test_a_merged_anchor_can_be_reused(self):
        text = (
            "views:\n"
            "  front: {<<: &base {<<: {elements: [{key: p1}]}, elements: [{key: p2}]}}\n"
            "  rear: *base\n"
        )
        parsed = strict(text)
        assert [e.key for e in parsed.front.elements] == ["p2"]
        assert [e.key for e in parsed.rear.elements] == ["p2"]

    @pytest.mark.parametrize(
        ("field", "value", "limit"),
        [
            ("at", "row", MAX_CANVAS_ROWS),
            ("at", "col", MAX_CANVAS_CELLS),
            ("span", "rows", MAX_CANVAS_ROWS),
            ("span", "cols", MAX_CANVAS_CELLS),
        ],
    )
    def test_element_position_and_span_are_bounded(self, field, value, limit):
        def place(n):
            def change(d):
                d["canvas"] = (
                    {"columns": MAX_CANVAS_CELLS, "rows": 1}
                    if limit == MAX_CANVAS_CELLS
                    else {"columns": 1, "rows": MAX_CANVAS_ROWS}
                )
                d["variants"] = {}
                front(d).update(rows=[], elements=[{"key": "p1", field: {value: n}}])

            return layout(change)

        strict(place(limit))
        with pytest.raises(LayoutParseError, match=f"{field}.{value}"):
            strict(place(limit + 1))

    def test_huge_generated_key_number_is_rejected(self):
        huge = 10**3000
        sequence = {"prefix": "p", "count": huge, "step": huge}
        with pytest.raises(LayoutParseError, match="prefix"):
            strict(layout(lambda d: front(d)["rows"].append({"sequence": sequence})))
