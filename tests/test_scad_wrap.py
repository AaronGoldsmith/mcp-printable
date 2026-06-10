"""Tests for scad_cross_section's compile-first slab placement.

The cross-section compiles user code to STL (so module/function/let
definitions are legal — issue #10), reads the real bounding box with trimesh,
and places the slab at percent of the actual extent (previously percent mapped
onto a fixed ±500mm presumed box, silently missing normal-sized parts).

These tests exercise the wrapping/positioning logic without invoking the
OpenSCAD binary.
"""

import numpy as np
import pytest
import trimesh

import scad_backend
from scad_backend import CompileResult, _numbered_source


class _FakeMesh:
    """Stands in for a trimesh mesh: 20mm cube spanning [-10, 10]^3."""
    bounds = np.array([[-10.0, -10.0, -10.0], [10.0, 10.0, 10.0]])
    faces = [0] * 12
    is_empty = False


@pytest.fixture
def capture(monkeypatch):
    """Stub compile/trimesh/render; return dict capturing the wrapper source."""
    captured = {}
    monkeypatch.setattr(
        scad_backend, "compile_to_stl",
        lambda code, timeout=120: CompileResult(ok=True, stl_path=r"C:\tmp\fake.stl"),
    )
    monkeypatch.setattr(trimesh, "load", lambda path, force=None: _FakeMesh())

    def fake_render_view(src, **kw):
        captured["src"] = src
        return b"png"

    monkeypatch.setattr(scad_backend, "render_view", fake_render_view)
    return captured


class TestSlabPlacement:
    def test_percent_maps_onto_real_bounds(self, capture):
        _, info = scad_backend.cross_section("cube(20, center=true);",
                                             axis="z", percent=25)
        # 25% of [-10, 10] = -5.0, NOT -250 of a presumed 1000mm box
        assert info["position_mm"] == -5.0
        assert info["axis_min_mm"] == -10.0
        assert info["axis_max_mm"] == 10.0
        assert "-5.000" in capture["src"]

    def test_wrapper_imports_compiled_stl(self, capture):
        scad_backend.cross_section("cube(5);")
        assert 'import("C:/tmp/fake.stl"' in capture["src"]  # forward slashes
        assert "intersection()" in capture["src"]

    def test_slab_thickness_on_chosen_axis(self, capture):
        scad_backend.cross_section("cube(5);", axis="x", percent=50,
                                   slab_thickness=0.7)
        assert "[0.700, 40.000, 40.000]" in capture["src"]

    def test_invalid_axis_rejected(self, capture):
        with pytest.raises(ValueError):
            scad_backend.cross_section("cube(5);", axis="w")


class TestErrors:
    def test_compile_failure_echoes_numbered_user_source(self, monkeypatch):
        monkeypatch.setattr(
            scad_backend, "compile_to_stl",
            lambda code, timeout=120: CompileResult(
                ok=False, stderr="Parser error: syntax error, line 1"),
        )
        with pytest.raises(RuntimeError) as exc_info:
            scad_backend.cross_section("cube([5,5,5]")
        msg = str(exc_info.value)
        assert "Parser error" in msg
        assert "   1 | cube([5,5,5]" in msg

    def test_empty_mesh_rejected(self, monkeypatch):
        monkeypatch.setattr(
            scad_backend, "compile_to_stl",
            lambda code, timeout=120: CompileResult(ok=True, stl_path="x.stl"),
        )

        class _Empty:
            is_empty = True
            faces = []

        monkeypatch.setattr(trimesh, "load", lambda path, force=None: _Empty())
        with pytest.raises(RuntimeError, match="empty mesh"):
            scad_backend.cross_section("cube(0);")

    def test_numbered_source_format(self):
        assert _numbered_source("a\nb") == "   1 | a\n   2 | b"
