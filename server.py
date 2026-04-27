#!/usr/bin/env python3
"""Printable MCP Server.

AI-driven 3D modeling and print validation, FDM-focused. Agent-agnostic —
works with any MCP client (Claude Code, Goose, Cursor, Codex, ...).

Exposes two tool families:
- `blender_*` — Blender backend over TCP to the Printable Blender addon
  (default 127.0.0.1:9876). Requires Blender 3.6+ + addon enabled.
- `scad_*`    — OpenSCAD backend, shells out to the `openscad` CLI and uses
  trimesh for mesh validation. No addon, no TCP. Requires OpenSCAD installed.

Plus domain-knowledge documents as MCP resources (printable://... URIs).
Cross-backend handoff happens via STL — both backends import and export it.
"""

import json
import socket
import struct
import uuid
import base64
import io
import os
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("printable_mcp")

mcp = FastMCP(
    "printable_blender",
    instructions=(
        "Printable: AI-driven 3D modeling and print validation. Optimized for "
        "FDM-printable geometry. Two backends: `blender_*` (Blender + addon) "
        "and `scad_*` (OpenSCAD CLI + trimesh). Pick by what's installed and "
        "the task — Blender for direct manipulation/sculpting, OpenSCAD for "
        "code-first parametric. Cross-backend via STL (`blender_export_stl` -> "
        "`scad_import_stl`, or vice versa).\n\n"
        "BLENDER ALWAYS-ON RULES:\n"
        "- For a *fresh* modeling task, start with `blender_clear_scene` — it auto-sets units to mm. "
        "Building at the default 1m scale produces zero-volume meshes that pass watertight checks "
        "but are useless. If the user is iterating on an existing scene (\"tweak this hinge\", \"add "
        "a fillet\"), do NOT clear — call `blender_get_scene_info` first to confirm `units.is_mm` "
        "and orient yourself. `clear_scene` refuses to wipe a non-empty scene by default — pass "
        "`force=True` only when you genuinely want a fresh build over existing geometry.\n"
        "- Use `blender_boolean` for boolean operations. NEVER use `bpy.ops.object.join()` "
        "(creates internal faces) or raw bpy boolean modifiers in execute_code (failures "
        "are silent).\n"
        "- 1–3 operations per `blender_execute_code`, then `blender_mesh_health` to verify "
        "(watertight? face count sane? connected_components == 1?).\n"
        "- Renders show silhouettes only — use `blender_cross_section_gallery` to verify "
        "internal geometry truth.\n"
        "- For any joint/hinge, you MUST call `blender_check_clearance_sweep` before export.\n"
        "- Run `blender_full_printability_check` on every part before `blender_export_stl`.\n\n"
        "DESIGN LOOP: plan (compute coords -> print -> verify math) -> build (small steps -> "
        "mesh_health) -> verify (renders + cross-sections) -> validate (printability + "
        "clearance) -> export (bundled STL).\n\n"
        "OPENSCAD ALWAYS-ON RULES:\n"
        "- Use $fn=24 during iterative design, raise to 60+ for final export.\n"
        "- Cross-section verifies internal truth — `scad_render_views` shows the silhouette only.\n"
        "- After `scad_compile`, ALWAYS run `scad_validate_printability` on the STL. "
        "Watertight + winding_consistent + sane volume are the minimum bar.\n"
        "- Exporting separate parts: use a `part` variable + `if (part==\"...\")` "
        "blocks; compile each part to its own STL. Do not ship a fused-assembly "
        "STL as 'printable' — slicers will merge separate shells in one STL.\n"
        "- See `printable://design/print-in-place` — same rules as Blender.\n\n"
        "DOMAIN KNOWLEDGE — exposed as MCP resources. Fetch via `resources/read` "
        "(preferred) or read from `docs/` on the filesystem if your client doesn't "
        "support resources:\n"
        "- `printable://blender/design-loop` — full design loop, visual feedback "
        "hierarchy, common failure modes (monolithic execute_code, unit-scale bug, "
        "render black-out, boolean degenerate faces).\n"
        "- `printable://design/print-in-place` — FDM mechanism design rules (cardinal "
        "print-path rule, clearance values, ball-socket / hinge / snap-fit / living-"
        "hinge patterns, validation checklist). READ THIS for any task with moving parts.\n"
        "- `printable://blender/image-displacement` — 2D image to printable 3D relief.\n"
        "- `printable://blender/blender-app` — launch, restart, multi-instance setup."
    ),
)


# ---------------------------------------------------------------------------
# TCP client to Blender addon
# ---------------------------------------------------------------------------

class BlenderConnection:
    """TCP client that sends commands to the Blender addon."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None

    def _connect(self) -> None:
        if self.sock is not None:
            return
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
        except ConnectionRefusedError:
            self.sock = None
            raise ConnectionError(
                f"Cannot connect to Blender on {self.host}:{self.port}. "
                "Make sure Blender is running with the Printable Bridge addon enabled."
            )

    def _send_msg(self, obj: dict) -> None:
        data = json.dumps(obj).encode('utf-8')
        header = struct.pack('>I', len(data))
        self.sock.sendall(header + data)

    def _recv_msg(self) -> dict:
        header = self._recv_exact(4)
        length = struct.unpack('>I', header)[0]
        data = self._recv_exact(length)
        return json.loads(data.decode('utf-8'))

    def _recv_exact(self, n: int) -> bytes:
        buf = b''
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Blender closed the connection")
            buf += chunk
        return buf

    def send(self, command: str, params: dict | None = None, timeout: float = 120) -> dict:
        """Send a command to Blender and return the result."""
        try:
            self._connect()
        except ConnectionError:
            self.sock = None
            raise

        msg = {
            'id': str(uuid.uuid4()),
            'command': command,
            'params': params or {},
        }

        try:
            self.sock.settimeout(timeout)
            self._send_msg(msg)
            response = self._recv_msg()
        except (ConnectionError, OSError, socket.timeout) as e:
            self.sock = None
            raise ConnectionError(f"Lost connection to Blender: {e}")

        if response.get('status') == 'error':
            error_msg = response.get('error', 'Unknown error')
            logger.error("Blender error: %s\n%s", error_msg, response.get('traceback', ''))
            raise RuntimeError(f"Blender error: {error_msg}")

        return response.get('result', {})


blender = BlenderConnection()


# ---------------------------------------------------------------------------
# Image compositing helpers (PIL on the server side)
# ---------------------------------------------------------------------------

def _tile_images(renders: list[dict], columns: int = 3,
                 tile_w: int = 400, tile_h: int = 400,
                 label_height: int = 28) -> str:
    """Composite multiple renders into a single labeled grid image.

    renders: list of {'label': str, 'image': base64_str}
    Returns: base64-encoded PNG of the composite.
    """
    from PIL import Image, ImageDraw, ImageFont

    n = len(renders)
    if n == 0:
        return ""
    cols = min(columns, n)
    rows = (n + cols - 1) // cols

    width = cols * tile_w
    height = rows * (tile_h + label_height)
    canvas = Image.new('RGB', (width, height), (30, 30, 40))
    draw = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except (OSError, IOError):
        font = ImageFont.load_default()

    for i, render in enumerate(renders):
        row, col = divmod(i, cols)
        x = col * tile_w
        y = row * (tile_h + label_height)

        # Label bar
        draw.rectangle([x, y, x + tile_w - 1, y + label_height - 1], fill=(20, 20, 35))
        draw.text((x + 8, y + 4), render['label'], fill=(0, 200, 200), font=font)

        # Image
        img_data = base64.b64decode(render['image'])
        img = Image.open(io.BytesIO(img_data))
        img = img.resize((tile_w, tile_h), Image.LANCZOS)
        canvas.paste(img, (x, y + label_height))

    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _side_by_side(before_b64: str, after_b64: str,
                  width: int = 400, height: int = 400) -> str:
    """Create a before/after side-by-side image."""
    from PIL import Image, ImageDraw, ImageFont

    label_h = 28
    canvas = Image.new('RGB', (width * 2, height + label_h), (30, 30, 40))
    draw = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except (OSError, IOError):
        font = ImageFont.load_default()

    for i, (b64, label) in enumerate([(before_b64, "BEFORE"), (after_b64, "AFTER")]):
        x = i * width
        draw.rectangle([x, 0, x + width - 1, label_h - 1], fill=(20, 20, 35))
        color = (200, 200, 0) if i == 0 else (0, 200, 100)
        draw.text((x + 8, 4), label, fill=color, font=font)

        img_data = base64.b64decode(b64)
        img = Image.open(io.BytesIO(img_data)).resize((width, height), Image.LANCZOS)
        canvas.paste(img, (x, label_h))

    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _image_content(b64: str):
    """Create an MCP ImageContent object from base64 PNG data."""
    from mcp.types import ImageContent
    return ImageContent(type="image", data=b64, mimeType="image/png")


def _text_and_image(text: str, image_b64: str):
    """Return both text and image content."""
    from mcp.types import TextContent, ImageContent
    return [
        TextContent(type="text", text=text),
        ImageContent(type="image", data=image_b64, mimeType="image/png"),
    ]


# ---------------------------------------------------------------------------
# MCP resources — domain-knowledge docs exposed as printable://... URIs
#
# Why resources (vs. filesystem paths): agents that support MCP resources can
# pull docs via the protocol regardless of where the server runs (local, remote,
# container). Paths in `instructions` are a fallback for clients without
# resource support.
# ---------------------------------------------------------------------------

_DOCS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")


def _read_doc(rel_path: str) -> str:
    """Read a markdown doc from docs/, with a friendly error if missing."""
    path = os.path.join(_DOCS_ROOT, rel_path)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"# Not found\n\nExpected doc at `docs/{rel_path}` but it was not shipped with this server."


@mcp.resource("printable://design/print-in-place", mime_type="text/markdown",
              name="Print-in-Place Design (FDM, backend-agnostic)",
              description="Cardinal print-path rule, clearance values, ball-socket/hinge/snap-fit patterns, validation checklist. READ FIRST for any moving-parts mechanism.")
def _res_print_in_place() -> str:
    return _read_doc("design/print-in-place.md")


@mcp.resource("printable://blender/design-loop", mime_type="text/markdown",
              name="Blender Design Loop",
              description="Plan→build→verify→validate→export workflow, boolean rules, unit safety, visual feedback hierarchy, common failure modes.")
def _res_blender_design_loop() -> str:
    return _read_doc("blender/design-loop.md")


@mcp.resource("printable://blender/image-displacement", mime_type="text/markdown",
              name="Blender: Image Displacement to Printable Relief",
              description="Turn a 2D image into raised printable 3D relief on a Blender primitive. PIL preprocessing, displace modifier setup, calibration table.")
def _res_blender_displacement() -> str:
    return _read_doc("blender/image-displacement.md")


@mcp.resource("printable://blender/blender-app", mime_type="text/markdown",
              name="Blender App Control",
              description="How to launch, restart, kill, and run multiple Blender instances for the Printable MCP.")
def _res_blender_app() -> str:
    return _read_doc("blender/blender-app.md")


@mcp.resource("printable://openscad/backend", mime_type="text/markdown",
              name="OpenSCAD Backend Reference",
              description="Setup, tool reference, validator details, cross-backend handoff, and always-on rules for the OpenSCAD backend (scad_* tools).")
def _res_openscad_backend() -> str:
    return _read_doc("openscad/README.md")


# ---------------------------------------------------------------------------
# Scene tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="blender_get_scene_info",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_get_scene_info() -> str:
    """Get a summary of all objects in the Blender scene: names, types, dimensions, vertex counts."""
    result = blender.send("get_scene_info")
    return json.dumps(result, indent=2)


@mcp.tool(
    name="blender_get_object_info",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_get_object_info(name: str) -> str:
    """Get detailed info about a specific object: dimensions, mesh stats, modifiers, materials, manifold check."""
    result = blender.send("get_object_info", {"name": name})
    return json.dumps(result, indent=2)


@mcp.tool(
    name="blender_clear_scene",
    annotations={"readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_clear_scene(force: bool = False) -> str:
    """Remove all objects from the scene. Refuses to wipe a non-empty scene unless force=True.

    Default behavior protects in-progress user work — if the scene already has
    objects, this tool returns an error listing them and asks the agent to
    confirm intent. Pass `force=True` to override (for genuine fresh-build
    scenarios). Always sets units to mm regardless.
    """
    if not force:
        info = blender.send("get_scene_info")
        objs = info.get("objects") or []
        if objs:
            names = [o.get("name", "?") for o in objs[:10]]
            more = "" if len(objs) <= 10 else f" (+{len(objs) - 10} more)"
            raise ValueError(
                f"Scene already has {len(objs)} object(s): {', '.join(names)}{more}. "
                f"Refusing to clear — this would destroy in-progress work. "
                f"Pass force=True if you really mean to wipe the scene, or use "
                f"blender_get_scene_info / blender_get_object_info to inspect "
                f"existing geometry and iterate on it instead."
            )
    blender.send("clear_scene")
    return "Scene cleared."


@mcp.tool(
    name="blender_rename_object",
    annotations={"readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_rename_object(old_name: str, new_name: str) -> str:
    """Rename an object (e.g., 'Cylinder.001' -> 'hinge_barrel')."""
    result = blender.send("rename_object", {"old_name": old_name, "new_name": new_name})
    return f"Renamed '{result['old_name']}' -> '{result['new_name']}'"


# ---------------------------------------------------------------------------
# Boolean operation (typed, safe)
# ---------------------------------------------------------------------------

@mcp.tool(
    name="blender_boolean",
    annotations={"readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": False},
)
async def blender_boolean(
    target: str,
    cutter: str,
    operation: str = "DIFFERENCE",
    keep_cutter: bool = False,
    solver: str = "EXACT",
) -> str:
    """Perform a boolean operation on two mesh objects, with built-in safety checks.

    PREFER THIS OVER execute_code for booleans — it handles context management,
    modifier ordering, and automatically checks connectivity and manifold after.

    operation: DIFFERENCE (subtract), UNION (add), or INTERSECT (keep overlap)
    keep_cutter: if False (default), deletes the cutter object after the operation
    solver: EXACT (default, more reliable) or FAST (faster, less reliable)

    Returns face count before/after, connected components, and any warnings.
    WARNING means the boolean may have silently failed (check the numbers).
    """
    result = blender.send("boolean", {
        "target": target,
        "cutter": cutter,
        "operation": operation,
        "keep_cutter": keep_cutter,
        "solver": solver,
    })
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Code execution
# ---------------------------------------------------------------------------

@mcp.tool(
    name="blender_execute_code",
    annotations={"readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": False},
)
async def blender_execute_code(code: str) -> str:
    """Execute Python code in Blender (bpy, bmesh, mathutils, Vector, Matrix, Euler, math available).

    WHEN TO USE: Creating/modifying geometry, applying modifiers, boolean operations.
    IMPORTANT: Use boolean union (modifier type='BOOLEAN', operation='UNION') to combine
    geometry. NEVER use bpy.ops.object.join() — it creates internal faces and breaks the mesh.
    After calling this, always call blender_mesh_health to verify the result.
    Set __result__ to return a value. Keep code under 20 lines.

    Example — create a cylinder and boolean-union it onto an existing object:
        bpy.ops.mesh.primitive_cylinder_add(radius=4, depth=10, location=(0, 0, 5))
        cyl = bpy.context.active_object
        cyl.name = "knuckle_A"
        # Boolean union onto the plate:
        mod = plate.modifiers.new("union_knuckle", 'BOOLEAN')
        mod.operation = 'UNION'
        mod.object = cyl
        bpy.context.view_layer.objects.active = plate
        bpy.ops.object.modifier_apply(modifier="union_knuckle")
        bpy.data.objects.remove(cyl, do_unlink=True)
        __result__ = f"Unioned knuckle onto {plate.name}"
    """
    result = blender.send("execute_code", {"code": code})
    parts = []
    if result.get('stdout'):
        parts.append(result['stdout'])
    if result.get('stderr'):
        parts.append(f"STDERR:\n{result['stderr']}")
    if result.get('result'):
        parts.append(f"Result: {result['result']}")
    return '\n'.join(parts) if parts else "Code executed successfully (no output)."


# ---------------------------------------------------------------------------
# Visual feedback tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="blender_get_screenshot",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_get_screenshot(
    elevation: float = 35, azimuth: float = 45,
    width: int = 800, height: int = 800,
    focus_object: str | None = None,
    isolate: bool = False,
    zoom: float = 1.0,
) -> Any:
    """Capture a single screenshot from a specific camera angle.

    WHEN TO USE: When you need a specific viewpoint that render_tiled doesn't cover.
    For example, "show me the barrel connection from below" = elevation=-20, azimuth=45.
    Use focus_object to zoom into a specific part instead of framing the whole scene.
    Use isolate=True to hide other objects that create visual noise.

    elevation: degrees above horizontal (0=side, 90=top, -20=slightly below)
    azimuth: horizontal rotation (0=front, 90=right, 180=back, 270=left)
    focus_object: camera targets this object's bounds, not the whole scene
    isolate: hide everything except focus_object
    zoom: >1.0 crops tighter (2.0 = 2x zoom on the target)
    """
    params = {
        "elevation": elevation, "azimuth": azimuth,
        "width": width, "height": height,
        "zoom": zoom,
    }
    if focus_object:
        params["focus_object"] = focus_object
        params["isolate"] = isolate
    result = blender.send("get_screenshot", params)
    return _image_content(result['image'])


@mcp.tool(
    name="blender_render_tiled",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_render_tiled(
    angles: list[str] | None = None,
    focus_object: str | None = None,
    isolate: bool = False,
    zoom: float = 1.0,
) -> Any:
    """Render 4 angles (iso/front/right/top) in a single labeled grid image.

    WHEN TO USE: After every major modeling step. This is your primary feedback tool.
    Call it with no args for a scene overview. Use focus_object to inspect one part.
    If something looks wrong, follow up with cross_section_gallery for internal truth.

    angles: which views (default: iso, front, right, top). Available: iso, front, back, right, left, top
    focus_object: camera targets this object's bounds — use when inspecting a specific part
    isolate: hide everything except focus_object for a clean view
    zoom: >1.0 crops tighter on the target (e.g., 2.0 for detailed view of a barrel)
    """
    if angles is None:
        angles = ['iso', 'front', 'right', 'top']
    params = {"angles": angles, "zoom": zoom}
    if focus_object:
        params["focus_object"] = focus_object
        params["isolate"] = isolate
    result = blender.send("render_tiled", params)
    composite = _tile_images(result['renders'], columns=min(len(angles), 4))
    return _image_content(composite)


@mcp.tool(
    name="blender_render_turntable",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_render_turntable(
    object_name: str,
    steps: int = 8,
    elevation: float = 20,
    isolate: bool = True,
    zoom: float = 1.0,
) -> Any:
    """Render N evenly-spaced angles around a specific object in a tiled grid.

    WHEN TO USE: When you need to understand a feature from all sides — especially
    cylindrical geometry (barrels, pins, knuckles) where 4 fixed angles miss details.
    Also useful to verify knuckle gaps, chamfers, and barrel-to-plate connections.

    steps: number of angles (8 = every 45 degrees, 12 = every 30 degrees)
    elevation: camera height in degrees (20 = slightly above, 0 = eye level, -15 = below)
    isolate: hide other objects (default True — you usually want a clean view)
    zoom: >1.0 zooms in tighter on the object
    """
    result = blender.send("render_turntable", {
        "object_name": object_name, "steps": steps,
        "elevation": elevation, "isolate": isolate, "zoom": zoom,
    })
    cols = min(steps, 4)
    composite = _tile_images(result['renders'], columns=cols, tile_w=300, tile_h=300)
    text = f"Turntable: {object_name}, {steps} angles at {elevation}° elevation"
    return _text_and_image(text, composite)


@mcp.tool(
    name="blender_cross_section",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_cross_section(
    object_name: str, axis: str = "z", percent: float = 50,
) -> Any:
    """Cut an object with a plane and render the exposed internal face.

    WHEN TO USE: When renders show a shape but you can't tell if the INSIDE is correct.
    Critical for: pin holes (are they through?), wall thickness (solid or hollow?),
    knuckle interleave (do the barrels actually alternate?), clearance gaps (visible gap?).
    Renders only show silhouettes — cross-sections show the truth.

    axis: x, y, or z (which direction to cut)
    percent: 0-100 (where along the axis — 50 = middle, 25 = quarter way)
    """
    result = blender.send("cross_section", {
        "object_name": object_name, "axis": axis, "percent": percent,
    })
    text = f"Cross-section: {axis.upper()} axis at {percent}% (position: {result['cut_position_mm']}mm)"
    return _text_and_image(text, result['image'])


@mcp.tool(
    name="blender_cross_section_gallery",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_cross_section_gallery(
    object_name: str,
    axes: list[str] | None = None,
    percents: list[float] | None = None,
) -> Any:
    """Tiled grid of cross-sections at multiple positions along one or more axes.

    WHEN TO USE: After building complex internal geometry (knuckle interleave, pin holes,
    socket bores). This is the ONLY way to verify internal features — renders can't see inside.
    The X-axis slices are usually most informative for hinges (shows knuckle alternation).

    axes: which axes to slice along (default: all three)
    percents: where to cut on each axis (default: 10%, 30%, 50%, 70%, 90%)
    """
    if axes is None:
        axes = ['x', 'y', 'z']
    if percents is None:
        percents = [10, 30, 50, 70, 90]

    result = blender.send("cross_section_gallery", {
        "object_name": object_name, "axes": axes, "percents": percents,
    })
    cols = len(percents)
    composite = _tile_images(result['renders'], columns=cols, tile_w=300, tile_h=300)
    text = f"Cross-section gallery: {len(result['renders'])} slices across axes {axes}"
    return _text_and_image(text, composite)


@mcp.tool(
    name="blender_render_printability_heatmap",
    annotations={"readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_render_printability_heatmap(
    object_name: str,
    overhang_angle: float = 45.0,
    min_wall_mm: float = 0.8,
) -> Any:
    """Render the object with faces colored by printability issues.

    Red = overhang, Yellow = thin wall, Green = OK.
    Returns a multi-angle tiled image plus issue counts.
    """
    result = blender.send("render_printability_heatmap", {
        "object_name": object_name,
        "overhang_angle": overhang_angle,
        "min_wall_mm": min_wall_mm,
    })
    composite = _tile_images(result['renders'], columns=min(len(result['renders']), 4))
    return _text_and_image(result['summary'], composite)


@mcp.tool(
    name="blender_render_with_dimensions",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_render_with_dimensions(
    object_names: list[str] | None = None,
) -> Any:
    """Render the scene with bounding box dimension data for each object.

    Returns an isometric render plus dimension measurements for each object.
    """
    params = {}
    if object_names:
        params['object_names'] = object_names
    result = blender.send("render_with_dimensions", params)

    lines = ["Dimensions:"]
    for m in result['measurements']:
        d = m['dimensions_mm']
        lines.append(f"  {m['name']}: {d[0]}mm x {d[1]}mm x {d[2]}mm")

    return _text_and_image('\n'.join(lines), result['image'])


@mcp.tool(
    name="blender_render_before_after",
    annotations={"readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": False},
)
async def blender_render_before_after(code: str) -> Any:
    """Capture before/after screenshots around a modeling operation.

    Provide the bpy code to execute between captures. Returns a side-by-side comparison.
    """
    result = blender.send("render_before_after", {"code": code})
    composite = _side_by_side(result['before_image'], result['after_image'])

    parts = ["Before/After comparison:"]
    code_out = result.get('code_output', {})
    if code_out.get('stdout'):
        parts.append(code_out['stdout'])
    if code_out.get('stderr'):
        parts.append(f"STDERR: {code_out['stderr']}")

    return _text_and_image('\n'.join(parts), composite)


# ---------------------------------------------------------------------------
# Mesh health & intersection tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="blender_mesh_health",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_mesh_health(object_name: str) -> str:
    """Fast mesh health checkpoint — call after EVERY boolean operation.

    WHEN TO USE: Immediately after any execute_code that modifies geometry.
    WHAT TO CHECK in the response:
    - is_watertight should be True (False = boolean left holes)
    - non_manifold_edges should be 0 (>0 = geometry is broken, redo the boolean)
    - degenerate_faces should be 0 (>0 = zero-area faces from bad boolean)
    - dimensions_mm should match your design intent (wrong = boolean ate geometry)
    This is fast (<10ms). There is no reason to skip it.
    """
    result = blender.send("mesh_health", {"object_name": object_name})
    return json.dumps(result, indent=2)


@mcp.tool(
    name="blender_check_intersection",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_check_intersection(object_a: str, object_b: str) -> str:
    """Check if two objects' meshes physically overlap (geometry intersects).

    WHEN TO USE: After assembling multi-part designs. Intersection = broken geometry.
    Two separate objects should NEVER intersect — if they do, a boolean went wrong
    or parts were placed too close. Distinct from clearance (which measures distance
    between non-touching objects). Use this to validate that separate parts are truly separate.
    Returns intersects=True/False and the count of overlapping face pairs.
    """
    result = blender.send("check_intersection", {
        "object_a": object_a, "object_b": object_b,
    })
    return json.dumps(result, indent=2)


@mcp.tool(
    name="blender_check_retention",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_check_retention(
    moving_object: str,
    static_objects: list[str],
    direction: str = "+Z",
    displacement: float = 20.0,
) -> str:
    """Check whether a moving part is physically captive by simulating displacement.

    Translates moving_object by displacement mm in direction, then checks if it
    intersects any static_objects. Returns CAPTIVE if blocked, FREE if not.

    Use this to verify print-in-place retention mechanisms before printing:
    - Car body on axle: direction='+Z', static_objects=['wheel_FL','wheel_FR',...]
    - Ball in socket: direction='+Y' (or any direction), static_objects=['socket']
    - Hinge pin: direction='+X', static_objects=['barrel']

    Args:
        moving_object: Name of the part that should be captive
        static_objects: Names of parts that should block the moving part
        direction: Displacement direction: '+Z', '-Z', '+X', '-X', '+Y', '-Y'
        displacement: How far to displace in mm (default 20mm)
    """
    result = blender.send("check_retention", {
        "moving_object": moving_object,
        "static_objects": static_objects,
        "direction": direction,
        "displacement": displacement,
    })
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Print validation tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="blender_check_overhangs",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_check_overhangs(
    object_name: str, angle_threshold: float = 45.0,
) -> str:
    """Check for overhang faces that exceed the angle threshold from vertical.

    FDM printers struggle with overhangs beyond 45 degrees. Returns face count,
    worst angle, and Z-height of the worst overhang.
    """
    result = blender.send("check_overhangs", {
        "object_name": object_name, "angle_threshold": angle_threshold,
    })
    return json.dumps(result, indent=2)


@mcp.tool(
    name="blender_check_thin_walls",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_check_thin_walls(
    object_name: str, min_thickness_mm: float = 0.8,
) -> str:
    """Check for walls thinner than the minimum by raycasting through the mesh.

    Default threshold (0.8mm) is approximately 2x a 0.4mm nozzle width.
    Returns thin face count and the thinnest wall found.
    """
    result = blender.send("check_thin_walls", {
        "object_name": object_name, "min_thickness_mm": min_thickness_mm,
    })
    return json.dumps(result, indent=2)


@mcp.tool(
    name="blender_check_clearance",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_check_clearance(
    object_a: str, object_b: str, min_clearance_mm: float = 0.3,
) -> str:
    """Check minimum clearance between two objects.

    For print-in-place mechanisms, parts need sufficient gap to avoid fusing.
    Default 0.3mm is typical for FDM.
    """
    result = blender.send("check_clearance", {
        "object_a": object_a, "object_b": object_b,
        "min_clearance_mm": min_clearance_mm,
    })
    return json.dumps(result, indent=2)


@mcp.tool(
    name="blender_check_clearance_sweep",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_check_clearance_sweep(
    inner_object: str, outer_object: str,
    axis: str = "Z", steps: int = 36,
    min_clearance_mm: float = 0.3,
) -> str:
    """Rotate inner_object through 360 degrees and check clearance at each step.

    WHEN TO USE: MANDATORY for any joint, hinge, or articulating mechanism.
    This is the only way to verify the part can actually move without collision.
    A hinge that looks fine at 0 degrees may collide at 90 degrees.
    Returns the worst-case clearance and the exact angle where it occurs.
    If passes=False, the joint WILL fuse during printing.
    """
    result = blender.send("check_clearance_sweep", {
        "inner_object": inner_object, "outer_object": outer_object,
        "axis": axis, "steps": steps, "min_clearance_mm": min_clearance_mm,
    })
    return json.dumps(result, indent=2)


@mcp.tool(
    name="blender_full_printability_check",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_full_printability_check(
    object_name: str,
    overhang_angle: float = 45.0,
    min_wall_mm: float = 0.8,
    clearance_partners: list[str] | None = None,
    min_clearance_mm: float = 0.3,
) -> str:
    """Run ALL printability checks: overhangs, thin walls, mesh health, and clearance.

    WHEN TO USE: Before exporting STL — this is the final gate. Run on EVERY mesh object.
    If clearance_partners is set, also checks clearance between this object and partners.
    Returns a comprehensive PASS/FAIL verdict. Do not export if any object fails.
    """
    params = {
        "object_name": object_name,
        "overhang_angle": overhang_angle,
        "min_wall_mm": min_wall_mm,
        "min_clearance_mm": min_clearance_mm,
    }
    if clearance_partners:
        params["clearance_partners"] = clearance_partners
    result = blender.send("full_printability_check", params)
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Export / import tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="blender_export_stl",
    annotations={"readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_export_stl(
    path: str,
    object_name: str | None = None,
    object_names: list[str] | None = None,
    binary: bool = True,
) -> str:
    """Export mesh(es) to a single STL file for 3D printing.

    With no object args: exports ALL mesh objects bundled into one STL (most common for printing).
    object_name: export only one object.
    object_names: export specific objects bundled into one STL (e.g., ["leaf_A", "leaf_B", "pin"]).
    Warns about non-manifold edges.

    Relative paths are resolved against the MCP server's working directory (where
    you launched your agent) — NOT Blender's. Blender's cwd is typically its
    install dir, which is read-only on Windows; resolving here avoids silent
    "cannot open file" errors against `C:\\Program Files\\...`.
    """
    # Resolve here so the addon receives an absolute path under a writable dir.
    # os.path.abspath uses the MCP process's cwd, which is the user's project
    # dir when launched via uvx/claude — exactly what the agent intends.
    path = os.path.abspath(path)

    params = {"path": path, "binary": binary}
    if object_names:
        params["object_names"] = object_names
    elif object_name:
        params["object_name"] = object_name
    result = blender.send("export_stl", params)

    exported = result.get('objects_exported', [])
    parts = [f"Exported {len(exported)} objects to: {result['path']} ({result['size_bytes']} bytes)"]
    parts.append(f"  Objects: {', '.join(exported)}")
    for w in result.get('warnings', []):
        parts.append(w)
    return '\n'.join(parts)


@mcp.tool(
    name="blender_import_stl",
    annotations={"readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": False},
)
async def blender_import_stl(path: str) -> str:
    """Import an STL file into the scene.

    Relative paths are resolved against the MCP server's working directory.
    """
    path = os.path.abspath(path)
    result = blender.send("import_stl", {"path": path})
    return (
        f"Imported '{result['object_name']}' from {result['path']}: "
        f"{result['vertices']} vertices, {result['faces']} faces"
    )


@mcp.tool(
    name="blender_save_blend",
    annotations={"readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_save_blend(path: str | None = None) -> str:
    """Save the current scene as a .blend file.

    If path is omitted, saves to the current file or a temp location.
    Relative paths are resolved against the MCP server's working directory.
    """
    params = {}
    if path:
        params["path"] = os.path.abspath(path)
    result = blender.send("save_blend", params)
    return f"Saved: {result['path']}"


# ---------------------------------------------------------------------------
# OpenSCAD backend tools — shell-out to the openscad CLI; trimesh for analysis.
# No addon, no TCP. Picked when Blender isn't available, or for code-first
# parametric work. Cross-backend handoff via STL.
# ---------------------------------------------------------------------------

import scad_backend


def _scad_unavailable_msg() -> str:
    return (
        "OpenSCAD CLI not found on this system. Install OpenSCAD "
        "(https://openscad.org), or set OPENSCAD_BIN to the executable path. "
        "The Blender backend is unaffected and remains usable."
    )


@mcp.tool(
    name="scad_compile",
    annotations={"readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def scad_compile(code: str, out_path: str | None = None,
                        timeout: int = 120) -> str:
    """Compile OpenSCAD code to an STL file via the CGAL renderer.

    Returns the STL path on success, or a structured error on failure. Use
    `scad_validate_printability` to inspect the resulting mesh.
    """
    if scad_backend.find_openscad() is None:
        return _scad_unavailable_msg()
    result = scad_backend.compile_to_stl(code, out_path=out_path, timeout=timeout)
    if not result.ok:
        return (
            f"COMPILE FAILED ({result.duration_s:.2f}s)\n\n"
            f"stderr:\n{result.stderr}"
        )
    size_kb = os.path.getsize(result.stl_path) / 1024.0
    return (
        f"OK  ({result.duration_s:.2f}s)\n"
        f"STL: {result.stl_path}  ({size_kb:.1f} KB)\n"
        f"warnings:\n{result.stderr.strip() or '(none)'}"
    )


@mcp.tool(
    name="scad_render_views",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def scad_render_views(code: str, views: list[str] | None = None,
                              size: int = 400, preview: bool = True):
    """Render multiple labeled views of an OpenSCAD model into a grid image.

    Default views: iso, front, right, top. Set `preview=False` for CGAL-
    rendered final views (slower).
    """
    if scad_backend.find_openscad() is None:
        return _scad_unavailable_msg()
    if not views:
        views = ["iso", "front", "right", "top"]

    renders = []
    errors = []
    for v in views:
        try:
            png_bytes = scad_backend.render_view(code, view=v, size=size,
                                                   preview=preview)
            renders.append({
                "label": v.upper(),
                "image": base64.b64encode(png_bytes).decode("ascii"),
            })
        except Exception as exc:
            errors.append(f"{v}: {exc}")

    if not renders:
        return "All views failed:\n" + "\n".join(errors)

    composite = _tile_images(renders, columns=2, tile_w=size, tile_h=size)
    text = f"Rendered {len(renders)}/{len(views)} views"
    if errors:
        text += " (errors: " + "; ".join(errors) + ")"
    return _text_and_image(text, composite)


@mcp.tool(
    name="scad_cross_section",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def scad_cross_section(code: str, axis: str = "z", percent: float = 50.0,
                               view: str = "iso", size: int = 512,
                               slab_thickness: float = 0.5):
    """Slice the model with a thin slab and render the result.

    Use this to verify INTERNAL geometry (clearances, hollows, joints) — the
    only reliable way to confirm what renders alone can't show.
    """
    if scad_backend.find_openscad() is None:
        return _scad_unavailable_msg()
    try:
        png_bytes = scad_backend.cross_section(
            code, axis=axis, percent=percent, view=view, size=size,
            slab_thickness=slab_thickness,
        )
    except Exception as exc:
        return f"Cross-section failed: {exc}"
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return _text_and_image(
        f"Cross-section axis={axis} at {percent:.1f}%, slab {slab_thickness}mm",
        b64,
    )


@mcp.tool(
    name="scad_validate_printability",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def scad_validate_printability(stl_path: str,
                                      max_overhang_deg: float = 45.0,
                                      min_volume_mm3: float = 1.0) -> str:
    """Run watertight / manifold / volume / overhang checks on an STL.

    Uses trimesh under the hood. Returns a PASS/WARN/FAIL verdict plus a
    structured report. Run this on the STL output of `scad_compile`.
    """
    try:
        report = scad_backend.validate_printability(
            stl_path,
            max_overhang_deg=max_overhang_deg,
            min_volume_mm3=min_volume_mm3,
        )
    except Exception as exc:
        return f"Validation failed: {exc}"

    oh = report.overhangs
    lines = [
        f"VERDICT: {report.verdict}",
        "",
        f"  file:        {report.file}",
        f"  watertight:  {report.is_watertight}",
        f"  windings ok: {report.is_winding_consistent}",
        f"  euler:       {report.euler_number}",
        f"  bodies:      {report.body_count}",
        f"  faces/verts: {report.face_count} / {report.vertex_count}",
        f"  volume:      {report.volume_mm3} mm^3",
        f"  surface:     {report.surface_area_mm2} mm^2",
        f"  bbox:        {report.dimensions_mm} mm",
        "",
        f"  overhangs >{max_overhang_deg}deg:",
        f"    faces:     {oh.overhang_face_count}",
        f"    area:      {oh.overhang_area_mm2} / {oh.total_area_mm2} mm^2 "
        f"({oh.overhang_pct_by_area}%)",
        f"    worst:     {oh.worst_angle_from_down_deg}deg from down",
    ]
    if report.issues:
        lines += ["", "issues:"] + [f"  - {x}" for x in report.issues]
    return "\n".join(lines)


@mcp.tool(
    name="scad_import_stl",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def scad_import_stl(stl_path: str, convexity: int = 10) -> str:
    """Return an OpenSCAD snippet that imports the given STL file.

    Use this to bring a Blender-exported (or any other) STL into a SCAD model
    for further parametric modification. See the returned snippet's NOTE for
    the --render-mode path caveat.
    """
    if not os.path.isfile(stl_path):
        return f"STL not found: {stl_path}"
    return scad_backend.import_stl_snippet(stl_path, convexity=convexity)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run()


if __name__ == "__main__":
    main()
