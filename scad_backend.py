"""OpenSCAD backend for the Printable MCP.

Self-contained: shells out to the `openscad` CLI for compile/render/cross-section,
uses `trimesh` for mesh I/O, manifold/watertight checks, volume, and bbox, and
adds a small overhang analysis on top of `trimesh.face_normals`.

No Blender, no addon, no TCP. The agent picks this backend when OpenSCAD is the
right tool (parametric, code-first) or when Blender isn't installed.

Cross-backend handoff happens via STL: SCAD writes STL natively, Blender imports
STL. Same mesh-quality bar both directions.
"""
from __future__ import annotations

import base64
import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# OpenSCAD CLI discovery
# ---------------------------------------------------------------------------

_OPENSCAD_CANDIDATES = [
    "openscad",  # PATH
    r"C:\Program Files\OpenSCAD\openscad.exe",
    r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
    "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
    "/usr/bin/openscad",
    "/usr/local/bin/openscad",
]


def find_openscad() -> Optional[str]:
    """Locate the openscad CLI. Returns the path, or None if not found."""
    env = os.environ.get("OPENSCAD_BIN")
    if env and os.path.isfile(env):
        return env
    for candidate in _OPENSCAD_CANDIDATES:
        resolved = shutil.which(candidate) if os.sep not in candidate else candidate
        if resolved and os.path.isfile(resolved):
            return resolved
    return None


def _run_openscad(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Invoke the openscad CLI. Raises a clear error if not installed."""
    binary = find_openscad()
    if binary is None:
        raise RuntimeError(
            "OpenSCAD CLI not found. Install OpenSCAD (https://openscad.org) "
            "or set OPENSCAD_BIN to the executable path. Searched: PATH, "
            "Program Files (Win), /Applications (macOS), /usr/[local/]bin (Linux)."
        )
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------

@dataclass
class CompileResult:
    ok: bool
    stl_path: Optional[str] = None
    stderr: str = ""
    duration_s: float = 0.0


def compile_to_stl(code: str, out_path: Optional[str] = None,
                   timeout: int = 120) -> CompileResult:
    """Render OpenSCAD code to an STL file via CGAL.

    The agent doesn't need to manage temp dirs — pass `out_path=None` and we
    return a path under the system tempdir.
    """
    import time

    if out_path is None:
        out_path = os.path.join(tempfile.gettempdir(),
                                f"printable_scad_{os.getpid()}.stl")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".scad", delete=False,
                                      encoding="utf-8") as f:
        f.write(code)
        scad_path = f.name

    t0 = time.time()
    try:
        proc = _run_openscad(["-o", out_path, scad_path], timeout=timeout)
        duration = time.time() - t0
        if proc.returncode != 0 or not os.path.isfile(out_path):
            return CompileResult(ok=False, stderr=proc.stderr or proc.stdout,
                                  duration_s=duration)
        return CompileResult(ok=True, stl_path=out_path, stderr=proc.stderr,
                              duration_s=duration)
    finally:
        try:
            os.unlink(scad_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Render (multi-angle)
# ---------------------------------------------------------------------------

# OpenSCAD --camera takes: tx,ty,tz,rx,ry,rz,distance
# Camera distance is auto-scaled by --autocenter --viewall, so distance is
# secondary — the rotation is what matters for view selection.
_VIEW_CAMERAS = {
    "iso":   "0,0,0,55,0,25,0",
    "front": "0,0,0,90,0,0,0",
    "back":  "0,0,0,90,0,180,0",
    "right": "0,0,0,90,0,90,0",
    "left":  "0,0,0,90,0,270,0",
    "top":   "0,0,0,0,0,0,0",
    "bottom":"0,0,0,180,0,0,0",
}


def render_view(code: str, view: str = "iso", size: int = 512,
                preview: bool = True, timeout: int = 60) -> bytes:
    """Render a single view as PNG bytes.

    `preview=True` uses OpenCSG (fast, no CGAL) — appropriate for iterative
    design. Set `preview=False` for CGAL-rendered final views.
    """
    if view not in _VIEW_CAMERAS:
        raise ValueError(f"Unknown view '{view}'. Choose from: {list(_VIEW_CAMERAS)}")

    out_path = os.path.join(tempfile.gettempdir(),
                             f"printable_scad_render_{os.getpid()}_{view}.png")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".scad", delete=False,
                                      encoding="utf-8") as f:
        f.write(code)
        scad_path = f.name

    args = [
        "-o", out_path,
        "--camera", _VIEW_CAMERAS[view],
        "--imgsize", f"{size},{size}",
        "--autocenter", "--viewall",
        "--colorscheme=Tomorrow",
    ]
    if preview:
        args.append("--preview")
    args.append(scad_path)

    try:
        proc = _run_openscad(args, timeout=timeout)
        if proc.returncode != 0 or not os.path.isfile(out_path):
            raise RuntimeError(
                f"OpenSCAD render failed for view '{view}'.\nstderr:\n"
                f"{proc.stderr or proc.stdout}"
            )
        with open(out_path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(scad_path)
        except OSError:
            pass
        try:
            os.unlink(out_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Cross-section
# ---------------------------------------------------------------------------

def cross_section(code: str, axis: str = "z", percent: float = 50.0,
                  view: str = "iso", size: int = 512,
                  slab_thickness: float = 0.5, timeout: int = 60) -> bytes:
    """Render a cross-section of the model by intersecting with a thin slab.

    The slab is a 1000mm cube (presumed to dwarf the model) centered at the
    chosen Z (or X/Y) plane, with thickness `slab_thickness`.

    Returns PNG bytes.
    """
    axis = axis.lower()
    if axis not in ("x", "y", "z"):
        raise ValueError("axis must be 'x', 'y', or 'z'")

    # Strategy: wrap user code in a module, compute the bbox at runtime is
    # hard from a shell; instead we use a giant slab and rely on viewall to
    # frame it. The slab is positioned at `percent` of a presumed-bounding box
    # range [-100, 100]mm — works for typical printable parts (<200mm).
    # Agents can override slab_thickness; agents needing precise positioning
    # should compile to STL first and use trimesh slicing instead.

    box = 1000.0
    half = box / 2.0
    # Map percent (0..100) onto [-half, +half] of the chosen axis.
    offset = -half + (percent / 100.0) * box

    if axis == "z":
        translate = f"[0, 0, {offset:.3f}]"
        scale = f"[{box}, {box}, {slab_thickness}]"
    elif axis == "y":
        translate = f"[0, {offset:.3f}, 0]"
        scale = f"[{box}, {slab_thickness}, {box}]"
    else:  # x
        translate = f"[{offset:.3f}, 0, 0]"
        scale = f"[{slab_thickness}, {box}, {box}]"

    wrapped = (
        "intersection() {\n"
        "  // ---- user model ----\n"
        f"  {{\n{code}\n  }}\n"
        "  // ---- slab ----\n"
        f"  translate({translate}) cube({scale}, center=true);\n"
        "}\n"
    )
    return render_view(wrapped, view=view, size=size, preview=True,
                        timeout=timeout)


# ---------------------------------------------------------------------------
# Mesh validation (trimesh + overhang on top of face_normals)
# ---------------------------------------------------------------------------

@dataclass
class OverhangStats:
    overhang_face_count: int = 0
    overhang_area_mm2: float = 0.0
    total_area_mm2: float = 0.0
    overhang_pct_by_area: float = 0.0
    worst_angle_from_down_deg: float = 90.0
    max_overhang_deg: float = 45.0


@dataclass
class PrintabilityReport:
    file: str
    is_watertight: bool = False
    is_winding_consistent: bool = False
    euler_number: int = 0
    volume_mm3: float = 0.0
    surface_area_mm2: float = 0.0
    bbox_min: list[float] = field(default_factory=list)
    bbox_max: list[float] = field(default_factory=list)
    dimensions_mm: list[float] = field(default_factory=list)
    body_count: int = 0
    face_count: int = 0
    vertex_count: int = 0
    overhangs: OverhangStats = field(default_factory=OverhangStats)
    verdict: str = "UNKNOWN"  # PASS | WARN | FAIL
    issues: list[str] = field(default_factory=list)


def _analyze_overhangs(mesh, max_overhang_deg: float = 45.0,
                        build_plate_z_tol: float = 0.1) -> OverhangStats:
    """Compute overhang stats from face normals + areas.

    Convention matches PrusaSlicer/Cura: angle = degrees from straight-down
    (-Z). 0deg = face points straight at the build plate (worst overhang),
    90deg = vertical face (no overhang), 180deg = face points up (no overhang).
    A face is an overhang when angle < max_overhang_deg.
    """
    import numpy as np

    normals = np.asarray(mesh.face_normals)
    areas = np.asarray(mesh.area_faces)
    centroids = np.asarray(mesh.triangles_center)

    if len(normals) == 0:
        return OverhangStats(max_overhang_deg=max_overhang_deg)

    down = np.array([0.0, 0.0, -1.0])
    cos_from_down = np.clip(normals @ down, -1.0, 1.0)
    angles = np.degrees(np.arccos(cos_from_down))

    # Skip build-plate faces (sitting on the bed pointing down).
    bbox_zmin = float(mesh.bounds[0][2])
    is_build_plate = (
        (centroids[:, 2] - bbox_zmin < build_plate_z_tol) &
        (cos_from_down > 0.9)
    )
    is_overhang = (angles < max_overhang_deg) & ~is_build_plate

    total_area = float(np.sum(areas))
    oh_area = float(np.sum(areas[is_overhang]))
    worst = 90.0
    if np.any(is_overhang):
        worst = float(np.min(angles[is_overhang]))

    return OverhangStats(
        overhang_face_count=int(np.sum(is_overhang)),
        overhang_area_mm2=round(oh_area, 2),
        total_area_mm2=round(total_area, 2),
        overhang_pct_by_area=round(oh_area / total_area * 100.0, 2)
            if total_area > 0 else 0.0,
        worst_angle_from_down_deg=round(worst, 2),
        max_overhang_deg=max_overhang_deg,
    )


def validate_printability(stl_path: str, max_overhang_deg: float = 45.0,
                           min_volume_mm3: float = 1.0) -> PrintabilityReport:
    """Full mesh-quality + printability check on an STL.

    Uses trimesh for: watertight, winding consistency, Euler number, volume,
    bbox, body (split) count, face/vertex counts. Adds an overhang pass on
    top of trimesh.face_normals.
    """
    import trimesh  # imported lazily so the Blender-only path doesn't pay

    mesh = trimesh.load(stl_path, force="mesh")
    if mesh.is_empty:
        return PrintabilityReport(file=stl_path, verdict="FAIL",
                                   issues=["mesh has zero faces"])

    bbox_min = mesh.bounds[0].tolist()
    bbox_max = mesh.bounds[1].tolist()
    dims = (mesh.bounds[1] - mesh.bounds[0]).tolist()

    # Body count via split (returns separate connected components).
    try:
        bodies = mesh.split(only_watertight=False)
        body_count = len(bodies) if bodies else 1
    except Exception:
        body_count = 1

    overhangs = _analyze_overhangs(mesh, max_overhang_deg=max_overhang_deg)

    report = PrintabilityReport(
        file=stl_path,
        is_watertight=bool(mesh.is_watertight),
        is_winding_consistent=bool(mesh.is_winding_consistent),
        euler_number=int(mesh.euler_number),
        volume_mm3=round(float(abs(mesh.volume)), 2),
        surface_area_mm2=round(float(mesh.area), 2),
        bbox_min=[round(v, 3) for v in bbox_min],
        bbox_max=[round(v, 3) for v in bbox_max],
        dimensions_mm=[round(v, 3) for v in dims],
        body_count=body_count,
        face_count=int(len(mesh.faces)),
        vertex_count=int(len(mesh.vertices)),
        overhangs=overhangs,
    )

    # Verdict
    issues: list[str] = []
    if not report.is_watertight:
        issues.append("mesh is NOT watertight (has holes or non-manifold edges)")
    if not report.is_winding_consistent:
        issues.append("face winding is inconsistent (some normals point inward)")
    if report.volume_mm3 < min_volume_mm3:
        issues.append(
            f"volume {report.volume_mm3} mm^3 is below minimum {min_volume_mm3} "
            "(units bug? unit-scale models are a common failure)"
        )
    if overhangs.overhang_pct_by_area > 30.0:
        issues.append(
            f"{overhangs.overhang_pct_by_area:.1f}% of surface area is overhang "
            f"(>{max_overhang_deg} deg from down). Worst face: "
            f"{overhangs.worst_angle_from_down_deg:.1f} deg."
        )

    report.issues = issues
    if not issues:
        report.verdict = "PASS"
    elif report.is_watertight and report.volume_mm3 >= min_volume_mm3:
        report.verdict = "WARN"
    else:
        report.verdict = "FAIL"

    return report


# ---------------------------------------------------------------------------
# STL import wrapper
# ---------------------------------------------------------------------------

def import_stl_snippet(stl_path: str, convexity: int = 10) -> str:
    """Return a SCAD snippet that imports the given STL.

    Caveat (from materialize learnings): in CGAL --render mode, openscad
    silently produces empty output if the STL path is absolute. Preview mode
    is fine. We emit an absolute path here; if the agent needs --render mode,
    it should copy the STL adjacent to the .scad and use a relative name.
    """
    abs_path = os.path.abspath(stl_path)
    posix = abs_path.replace("\\", "/")
    return (
        f'import("{posix}", convexity={convexity});\n'
        '// NOTE: works reliably in --preview mode. For --render (CGAL),\n'
        '// place the STL next to the .scad and use a relative path.\n'
    )


# ---------------------------------------------------------------------------
# CLI / smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Tiny smoke test that does not require trimesh or openscad to import.
    binary = find_openscad()
    print(f"openscad binary: {binary or 'NOT FOUND'}")
    if binary:
        proc = _run_openscad(["--version"])
        print(proc.stderr.strip() or proc.stdout.strip())
