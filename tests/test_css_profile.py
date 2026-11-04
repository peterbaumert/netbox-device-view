"""
Tests for the CSS import profile that the Data Source import applies.

These are pure unit tests: no Django, no DB, no NetBox required.
"""

from pathlib import Path

import pytest

from netbox_device_view.layout import parse_yaml, render_css
from netbox_device_view.layout.css_profile import validate_css
from netbox_device_view.layout.parser import LayoutParseError

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
CSS_EXAMPLES = sorted(EXAMPLES.glob("*/*.css"))
YAML_EXAMPLES = sorted(EXAMPLES.glob("yaml/*/*.yaml"))

BASE = '.deviceview.area {\n  grid-template-areas: "p1 p2" "p3 .";\n}\n'


@pytest.mark.parametrize("example", CSS_EXAMPLES, ids=lambda p: p.name)
def test_bundled_css_examples_pass_unchanged(example):
    validate_css(example.read_text(encoding="utf-8-sig"))


@pytest.mark.parametrize("example", YAML_EXAMPLES, ids=lambda p: p.name)
def test_css_generated_from_bundled_yaml_passes(example):
    validate_css(render_css(parse_yaml(example.read_text(), strict=True)))


def test_base_css_passes():
    validate_css(BASE)


def rejected(css, match):
    with pytest.raises(LayoutParseError, match=match):
        validate_css(css)


@pytest.mark.parametrize(
    "css",
    [
        BASE + ".deviceview.area { grid-auto-rows: auto;",
        BASE.replace('"p3 ."', '"p3 .'),
        BASE + "/* not closed",
        BASE.replace("p1", "\\70 1"),
        BASE + "}",
        BASE.replace('"p3 ."', '"p3\n."'),
        BASE + "stray;",
        BASE + "stray",
        "(" * 100000 + ")" * 100000,
        BASE.replace("{", "{ background: " + "(" * 100000 + ")" * 100000 + ";"),
    ],
)
def test_parse_errors_are_rejected(css):
    rejected(css, "parse error")


def test_important_is_rejected():
    rejected(BASE.replace('.";', '." !important;'), "!important")


@pytest.mark.parametrize(
    "css",
    [
        "/* </style><script>alert(1)</script> */\n" + BASE,
        BASE.replace('"p3 ."', '"p3 <b>"'),
        "<!-- " + BASE + " -->",
        BASE.replace(".deviceview.area", ".deviceview > .area"),
        BASE + "<",
    ],
)
def test_html_delimiters_are_rejected(css):
    rejected(css, "HTML")


@pytest.mark.parametrize(
    ("css", "match"),
    [
        ('@import "x.css";\n' + BASE, "at-rule"),
        ("@media print { " + BASE + " }", "at-rule"),
        (BASE.replace("{", "{ @media print { x: y; }"), "at-rule"),
        (BASE.replace("{", "{ background: url(x.png);"), "URL"),
        (BASE.replace("{", '{ background: url("x.png");'), "URL"),
        (BASE.replace("{", "{ --color: #000;"), "custom properties"),
        (BASE.replace("{", "{ .other { background: #000; }"), "Nested rules"),
        (BASE.replace("{", "{ &.dFront { background: #000; }"), "Nested rules"),
    ],
)
def test_unsafe_constructs_are_rejected(css, match):
    rejected(css, match)


@pytest.mark.parametrize(
    "selector",
    [
        ".deviceview.area.dFront",
        ".deviceview.area.dRear",
        ".deviceview.moduleC9300-NM-8X.area",
        ".area.deviceview.module1G.dRear",
        "  .deviceview.area\n",
    ],
)
def test_accepted_selectors(selector):
    validate_css(BASE.replace(".deviceview.area ", selector))


@pytest.mark.parametrize(
    "selector",
    [
        ".deviceview .area",
        ".deviceview + .area",
        ".deviceview ~ .area",
        ".deviceview.area[title]",
        ".deviceview.area:hover",
        ".deviceview.area::before",
        ".deviceview.area, .deviceview.area.dFront",
        "div.deviceview.area",
        "#x.deviceview.area",
        "*.deviceview.area",
        ".deviceview",
        ".area",
        ".deviceview.area.other",
        ".deviceview.area.dFront.dRear",
        ".deviceview.area.moduleA.moduleB",
        ".deviceview.area.area",
        ".deviceview.area.module",
        ".deviceview.Area",
        ".deviceview.area.",
        "",
    ],
)
def test_rejected_selectors(selector):
    rejected(BASE.replace(".deviceview.area ", selector + " "), "selector")


def declared(declaration):
    return BASE.replace("{", "{\n  " + declaration)


@pytest.mark.parametrize(
    "declaration",
    [
        "grid-auto-rows: auto;",
        "grid-auto-columns: auto;",
        "background: #000;",
        "background-color: rgb(0 0 0 / 50%);",
        "background-color: red;",
        "background: /* dark */ #000;",
    ],
)
def test_accepted_declarations(declaration):
    validate_css(declared(declaration))


@pytest.mark.parametrize(
    ("declaration", "match"),
    [
        ("color: red;", "color: the property is not accepted"),
        ("grid-template-columns: auto;", "grid-template-columns: the property"),
        ("grid-auto-rows: 20px;", "grid-auto-rows: must be auto"),
        ("grid-auto-columns: auto auto;", "grid-auto-columns: must be auto"),
        ("background: linear-gradient(red, blue);", "background: must be one"),
        ("background: #000 no-repeat;", "background: must be one"),
        ("background-color: notacolor;", "background-color: must be one"),
        ("background-color: ;", "background-color: must be one"),
    ],
)
def test_rejected_declarations(declaration, match):
    rejected(declared(declaration), match)


@pytest.mark.parametrize(
    "areas",
    [
        '"p1 p2" "p3"',
        '""',
        '"  "',
        "none",
        "",
        '"1p"',
        '"pé"',
        '"p1 .x"',
        '"p1" none',
        "1",
        '"p1" 5%',
    ],
)
def test_invalid_grid_areas_are_rejected(areas):
    rejected(BASE.replace('"p1 p2" "p3 ."', areas), "grid-template-areas")


@pytest.mark.parametrize(
    ("areas", "area"),
    [
        ('"a b a"', "a"),
        ('"a a" "a b"', "a"),
        ('"a ." ". a"', "a"),
        ('"a b" "b a"', "a"),
        ('"x a a" "x a b" "x a a"', "a"),
    ],
)
def test_areas_that_are_not_rectangles_are_rejected(areas, area):
    rejected(
        BASE.replace('"p1 p2" "p3 ."', areas),
        f"grid-template-areas: the cells of area '{area}' must make one rectangle",
    )


@pytest.mark.parametrize(
    "areas",
    ['"a a" "a a"', '"a b" "a b"', '". a" ". a"', '"a a b" "c c b"', '"a" "a" "a"'],
)
def test_rectangular_areas_are_accepted(areas):
    validate_css(BASE.replace('"p1 p2" "p3 ."', areas))


def test_dot_runs_are_empty_cells():
    validate_css(BASE.replace('"p3 ."', '"p3 ..."'))


@pytest.mark.parametrize(
    "css",
    [
        ".deviceview.area { background: #000; }",
        '.deviceview.area { grid-template-areas: ". ..";\n}',
        "/* nothing */",
    ],
)
def test_one_named_grid_area_is_required(css):
    rejected(css, "at least one grid area")
