"""Tests for the scad_cross_section code-wrapping strategy (issue #10).

User code containing module/function/let definitions must survive the
slab-intersection wrap. These tests exercise the wrapping logic without
invoking the OpenSCAD binary.
"""

import pytest

import scad_backend
from scad_backend import _hoist_file_scope_statements, _numbered_source


class TestHoisting:
    def test_plain_code_unchanged(self):
        hoisted, body = _hoist_file_scope_statements("cube([5,5,5]);")
        assert hoisted == []
        assert body == "cube([5,5,5]);"

    def test_use_and_include_hoisted(self):
        code = "use <MCAD/gears.scad>\ninclude <helpers.scad>;\ncube(5);"
        hoisted, body = _hoist_file_scope_statements(code)
        assert hoisted == ["use <MCAD/gears.scad>", "include <helpers.scad>;"]
        assert body == "cube(5);"

    def test_module_definitions_stay_in_body(self):
        code = "module foo() { cube(5); }\nfoo();"
        hoisted, body = _hoist_file_scope_statements(code)
        assert hoisted == []
        assert body == code


class TestWrappedSource:
    """Verify the generated wrapper puts user code at module scope."""

    def _capture_wrapped(self, code, **kwargs):
        captured = {}

        def fake_render_view(src, **kw):
            captured["src"] = src
            return b"png"

        orig = scad_backend.render_view
        scad_backend.render_view = fake_render_view
        try:
            scad_backend.cross_section(code, **kwargs)
        finally:
            scad_backend.render_view = orig
        return captured["src"]

    def test_user_code_inside_module_body(self):
        src = self._capture_wrapped("module hex(d) { cylinder(d=d); }\nhex(8);")
        assert "module __printable_user_model__() {" in src
        assert "module hex(d)" in src
        assert "__printable_user_model__();" in src
        # Definitions must come before the intersection block
        assert src.index("module hex(d)") < src.index("intersection()")

    def test_includes_hoisted_to_file_scope(self):
        src = self._capture_wrapped("use <MCAD/gears.scad>\ncube(5);")
        assert src.index("use <MCAD/gears.scad>") < src.index("module __printable_user_model__")

    def test_slab_axis_and_percent(self):
        src = self._capture_wrapped("cube(5);", axis="x", percent=25)
        assert "[-250.000, 0, 0]" in src

    def test_invalid_axis_rejected(self):
        with pytest.raises(ValueError):
            scad_backend.cross_section("cube(5);", axis="w")


class TestErrorEcho:
    def test_render_failure_includes_numbered_source(self):
        def failing_render_view(src, **kw):
            raise RuntimeError("Parser error: syntax error in file tmp123.scad, line 4")

        orig = scad_backend.render_view
        scad_backend.render_view = failing_render_view
        try:
            with pytest.raises(RuntimeError) as exc_info:
                scad_backend.cross_section("cube([5,5,5]")
        finally:
            scad_backend.render_view = orig
        msg = str(exc_info.value)
        assert "Wrapped source as compiled" in msg
        assert "   1 |" in msg

    def test_numbered_source_format(self):
        out = _numbered_source("a\nb")
        assert out == "   1 | a\n   2 | b"
