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

# Server name kept as `printable_blender` for back-compat with installed Blender
# addons that bind to this exact module identifier. See docs/internals/naming.md
# for the four-name story (PyPI: mcp-printable, CLI: printable, server name +
# addon module: printable_blender, mcpServers entry key: freeform).
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
        "and orient yourself. `blender_clear_scene` refuses to wipe a non-empty scene by default — pass "
        "`force=True` only when you genuinely want a fresh build over existing geometry.\n"
        "- Use `blender_boolean` for boolean operations. NEVER use `bpy.ops.object.join()` "
        "(creates internal faces) or raw bpy boolean modifiers in execute_code (failures "
        "are silent).\n"
        "- 1–3 operations per `blender_execute_code`, then `blender_validate(checks=['HEALTH'])` to verify "
        "(watertight? face count sane? connected_components == 1?).\n"
        "- Renders show silhouettes only — use `blender_cross_section_gallery` to verify "
        "internal geometry truth.\n"
        "- For any joint/hinge, you MUST call `blender_check_clearance_sweep` before export.\n"
        "- Run `blender_validate(checks=['ALL'])` on every part before `blender_export_stl`.\n\n"
        "DESIGN LOOP: plan (compute coords -> print -> verify math) -> build (small steps -> "
        "validate) -> verify (renders + cross-sections) -> validate (printability + "
        "clearance) -> export (bundled STL).\n\n"
        "PRINTABILITY DOCTRINE — design for SUPPORT-FREE printing by default. When "
        "`blender_validate` or `scad_validate_printability` flags overhangs, prefer "
        "design changes over accepting supports: angle features >=45 deg from horizontal, "
        "flatten the print-bed surface (boolean a cube below the part), sink/chamfer "
        "overhangs into adjacent geometry, tilt or merge protruding features into the body. "
        "Treat \"this needs supports\" as a last resort — name the redesign you ruled out "
        "before suggesting supports. Inherent overhangs (a sphere's lower hemisphere) are "
        "the only legitimate exception and should be called out as such.\n\n"
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
        """Send a command to Blender and return the result.

        Opens a fresh TCP connection per command and closes it before returning,
        so multiple MCP clients can share the same Blender bridge without one
        client holding the socket and starving the others. The cost is one
        localhost TCP setup per command (sub-millisecond, lost in the noise of
        actual Blender work).
        """
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
        finally:
            # Always close the socket so the addon's accept loop can pick up
            # the next waiting client. Without this, whichever MCP server
            # connected first holds the bridge until its process exits.
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None

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
    name="blender_restore_checkpoint",
    annotations={"readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_restore_checkpoint() -> str:
    """Restore the scene from the auto-saved checkpoint — the undo button for a destroyed mesh.

    A checkpoint is saved automatically before every blender_boolean and
    blender_execute_code call, so this rolls back to the state just before the
    most recent mutating operation. Replaces ALL current scene objects.
    Use immediately after a DEGENERATE RESULT warning or a botched edit —
    a subsequent mutating call overwrites the checkpoint with the broken state.
    """
    result = blender.send("restore_checkpoint")
    return json.dumps(result, indent=2)


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
    use_self: bool = False,
    use_hole_tolerant: bool = False,
) -> str:
    """Boolean op on two meshes with built-in connectivity + manifold checks. Always prefer this over raw modifiers in execute_code.

    operation: DIFFERENCE | UNION | INTERSECT. solver: EXACT (reliable) | FAST.
    use_self (EXACT only): classify self-intersecting operands via winding numbers.
    Set True when an operand contains multiple overlapping shells (multi-shell
    meshes) — without it the EXACT solver can silently annihilate the target
    (an ANNIHILATION warning in the result flags this). Slower; default False.
    use_hole_tolerant (EXACT only): better results when operands have holes
    (non-watertight geometry). Slower; default False.
    Returns face counts, connected components, warnings. A WARNING means the
    boolean may have silently failed — inspect the numbers and re-run.
    """
    result = blender.send("boolean", {
        "target": target,
        "cutter": cutter,
        "operation": operation,
        "keep_cutter": keep_cutter,
        "solver": solver,
        "use_self": use_self,
        "use_hole_tolerant": use_hole_tolerant,
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
    """Run Python in Blender (bpy, bmesh, mathutils, math available). Set `__result__` to return a value. Keep under ~20 lines.

    For boolean ops use `blender_boolean` instead — it's safer and validates the
    result. NEVER use `bpy.ops.object.join()` (creates internal faces). After any
    geometry change call `blender_validate(checks=['HEALTH'])` to verify watertight + manifold.
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
    """Single screenshot from a custom camera angle. Use when render_tiled's 4 fixed views miss what you need.

    elevation: deg above horizontal (0=side, 90=top, negative=below).
    azimuth: deg rotation (0=front, 90=right, 180=back).
    focus_object + isolate: frame and isolate one part for a clean detail view.
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
    """4-angle labeled grid render — your primary feedback tool after every modeling step.

    Default views: iso/front/right/top. Available: iso, front, back, right, left, top.
    Use focus_object (+isolate) to zoom into a specific part. Follow up with
    cross_section_gallery if internal geometry needs verification.
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
    """N-angle turntable around one object. Use for cylindrical geometry (barrels, pins) where 4 fixed angles miss details.

    steps=8 → every 45°, steps=12 → every 30°. elevation in degrees (negative=below).
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
    object_name: str | None = None,
    axis: str = "z",
    percent: float = 50,
    object_names: list[str] | None = None,
) -> Any:
    """Cut and render the exposed internal face. Use to verify internal geometry that renders can't show: pin holes, wall thickness, knuckle interleave, clearance gaps.

    percent: 0-100, position along the chosen axis (50 = middle).
    object_names: cut multiple objects with the same plane and render them together. Essential for verifying chain joints, ball-in-socket captivity, or any pair where parts wrap around each other.

    Provide exactly one of object_name OR object_names — sending both raises ValueError to avoid silent precedence bugs.
    """
    if object_name and object_names:
        raise ValueError("Provide either object_name or object_names, not both")
    payload = {"axis": axis, "percent": percent}
    if object_names:
        payload["object_names"] = object_names
    elif object_name:
        payload["object_name"] = object_name
    else:
        raise ValueError("Provide either object_name or object_names")
    result = blender.send("cross_section", payload)
    suffix = f" ({result.get('object_count', 1)} objects)" if result.get('object_count', 1) > 1 else ""
    text = f"Cross-section: {axis.upper()} axis at {percent}% (position: {result['cut_position_mm']}mm){suffix}"
    return _text_and_image(text, result['image'])


@mcp.tool(
    name="blender_cross_section_gallery",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_cross_section_gallery(
    object_name: str | None = None,
    axes: list[str] | None = None,
    percents: list[float] | None = None,
    object_names: list[str] | None = None,
) -> Any:
    """Grid of cross-sections at multiple positions along one or more axes. The only way to verify complex internal geometry (knuckle interleave, pin holes, socket bores).

    Default: all 3 axes × [10, 30, 50, 70, 90]%. X-axis slices are usually most informative for hinges.
    object_names: cut multiple objects with the same planes and render each tile with all of them. Use for chain joints / mating-part captivity verification.

    Provide exactly one of object_name OR object_names — sending both raises ValueError to avoid silent precedence bugs.
    """
    if object_name and object_names:
        raise ValueError("Provide either object_name or object_names, not both")
    if axes is None:
        axes = ['x', 'y', 'z']
    if percents is None:
        percents = [10, 30, 50, 70, 90]

    payload = {"axes": axes, "percents": percents}
    if object_names:
        payload["object_names"] = object_names
    elif object_name:
        payload["object_name"] = object_name
    else:
        raise ValueError("Provide either object_name or object_names")
    result = blender.send("cross_section_gallery", payload)
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
# Intersection & retention tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="blender_check_intersection",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_check_intersection(object_a: str, object_b: str) -> str:
    """Check if two meshes physically overlap. Use after assembly — separate parts should NEVER volumetrically overlap (would mean a boolean went wrong, or parts were placed too close).

    Distinct from clearance (which measures distance between non-touching objects).
    Returns contact_type: NONE | SURFACE_CONTACT (coincident faces, expected for
    flush-fit assemblies) | VOLUMETRIC_OVERLAP (parts share volume and will fuse),
    plus overlap_volume_mm3 and the raw face-pair count.
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
    """Verify a moving part is captive by translating it `displacement` mm in `direction` and checking intersection with `static_objects`. Returns CAPTIVE or FREE.

    direction: '+X'/'-X'/'+Y'/'-Y'/'+Z'/'-Z'. Use for: car body on axles, ball in socket, hinge pin in barrel.
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
    name="blender_check_clearance",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_check_clearance(
    object_a: str, object_b: str, min_clearance_mm: float = 0.3,
) -> str:
    """Minimum gap between two objects. Default 0.3mm is typical FDM print-in-place clearance — below this, parts fuse."""
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
    """Rotate inner_object through 360° and check clearance at each step. MANDATORY for any joint, hinge, or articulating mechanism.

    A hinge that looks fine at 0° may collide at 90°. Returns worst-case
    clearance and the angle where it occurs. passes=False → the joint WILL
    fuse during printing.
    """
    result = blender.send("check_clearance_sweep", {
        "inner_object": inner_object, "outer_object": outer_object,
        "axis": axis, "steps": steps, "min_clearance_mm": min_clearance_mm,
    })
    return json.dumps(result, indent=2)


@mcp.tool(
    name="blender_validate",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def blender_validate(
    object_name: str,
    checks: list[str] | None = None,
    overhang_angle: float = 45.0,
    min_wall_mm: float = 0.8,
    clearance_partners: list[str] | None = None,
    min_clearance_mm: float = 0.3,
    verbose: bool = False,
) -> str:
    """Run specified printability checks on a mesh. Valid checks: 'ALL', 'HEALTH', 'OVERHANGS', 'THIN_WALLS', 'CLEARANCE'.

    Replaces individual mesh_health, overhang, and thin_wall checks. Use checks=['ALL'] to run the full printability suite before STL export.
    Set clearance_partners to check clearance against named neighbors.
    Per-face issue lists are capped at 10 exemplars; pass verbose=True for the full lists (can be very large on dense meshes).
    """
    params = {
        "object_name": object_name,
        "overhang_angle": overhang_angle,
        "min_wall_mm": min_wall_mm,
        "min_clearance_mm": min_clearance_mm,
        "verbose": verbose,
    }
    if checks:
        params["checks"] = checks
    if clearance_partners:
        params["clearance_partners"] = clearance_partners
    result = blender.send("validate", params)
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
    """Export mesh(es) to a single STL for 3D printing. Warns about non-manifold edges.

    With no object args: exports ALL mesh objects bundled into one STL (most
    common for printing). Use object_name for one part, object_names=[...] for
    a specific bundle. Relative paths resolve against the MCP server's cwd, not
    Blender's (which is its install dir, read-only on Windows).
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
async def scad_compile(code: str, output_path: str | None = None,
                        timeout: int = 120) -> str:
    """Compile OpenSCAD code to STL via CGAL. Always follow with `scad_validate_printability` on the result.

    output_path: relative paths resolve against MCP server cwd (so `"cube.stl"`
    lands in your project dir, not Blender's). Omit to write to a tempdir.
    """
    if scad_backend.find_openscad() is None:
        return _scad_unavailable_msg()
    if output_path is not None:
        output_path = os.path.abspath(output_path)
    result = scad_backend.compile_to_stl(code, output_path=output_path, timeout=timeout)
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
    """Slice the model with a thin slab and render the cut. The only reliable way to verify internal geometry (clearances, hollows, joints).

    percent: 0-100 along the chosen axis (mapped onto the model's actual bounds). slab_thickness in model units.
    Compiles the code to STL first, so expect CGAL render time on heavy models.
    """
    if scad_backend.find_openscad() is None:
        return _scad_unavailable_msg()
    try:
        png_bytes, info = scad_backend.cross_section(
            code, axis=axis, percent=percent, view=view, size=size,
            slab_thickness=slab_thickness,
        )
    except Exception as exc:
        return f"Cross-section failed: {exc}"
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return _text_and_image(
        f"Cross-section axis={axis} at {percent:.1f}% "
        f"(position: {info['position_mm']}mm, model spans "
        f"{info['axis_min_mm']}..{info['axis_max_mm']}mm), "
        f"slab {slab_thickness}mm",
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
    """Watertight / manifold / volume / overhang checks on an STL via trimesh. Run after every `scad_compile`.

    PASS/WARN/FAIL verdict + structured report.
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
    """Return an OpenSCAD snippet that imports the given STL — for Blender→SCAD handoff or further parametric modification.

    Returned snippet includes a NOTE about the --render-mode path caveat.
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
