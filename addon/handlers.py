"""Command handlers for the Printable Blender addon.

Each handler receives a params dict and returns a result dict.
Handlers run on Blender's main thread (dispatched via bpy.app.timers).
"""

import bpy
import bmesh
import math
import sys
import io
import os
import tempfile
import base64
import traceback
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import utils


def dispatch(command, params):
    """Route a command string to its handler function."""
    handler = HANDLERS.get(command)
    if handler is None:
        raise ValueError(f"Unknown command: '{command}'. Available: {list(HANDLERS.keys())}")
    return handler(params)


# ---------------------------------------------------------------------------
# Scene handlers
# ---------------------------------------------------------------------------

def handle_get_scene_info(params):
    objects = []
    for obj in bpy.context.scene.objects:
        info = {
            'name': obj.name,
            'type': obj.type,
            'location': list(obj.location),
            'rotation': list(obj.rotation_euler),
            'scale': list(obj.scale),
            'visible': obj.visible_get(),
        }
        if obj.type == 'MESH':
            info['vertices'] = len(obj.data.vertices)
            info['faces'] = len(obj.data.polygons)
            info['edges'] = len(obj.data.edges)
            # Bounding box dimensions
            bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
            dims = [
                max(v[i] for v in bb) - min(v[i] for v in bb)
                for i in range(3)
            ]
            info['dimensions_mm'] = [round(d, 2) for d in dims]
            info['modifiers'] = [m.name for m in obj.modifiers]
        objects.append(info)

    units = bpy.context.scene.unit_settings
    scale_ok = abs(units.scale_length - 0.001) < 1e-6
    return {
        'object_count': len(objects),
        'objects': objects,
        'active_object': bpy.context.active_object.name if bpy.context.active_object else None,
        'scene_name': bpy.context.scene.name,
        'units': {
            'system': units.system,
            'length_unit': units.length_unit,
            'scale_length': units.scale_length,
            'is_mm': scale_ok,
            'warning': None if scale_ok else f"scale_length={units.scale_length} — run blender_clear_scene to reset to mm, or dimensions will be wrong",
        },
    }


def handle_get_object_info(params):
    obj = utils.resolve_object(params['name'])
    info = {
        'name': obj.name,
        'type': obj.type,
        'location': list(obj.location),
        'rotation': list(obj.rotation_euler),
        'scale': list(obj.scale),
        'visible': obj.visible_get(),
        'parent': obj.parent.name if obj.parent else None,
        'children': [c.name for c in obj.children],
    }
    if obj.type == 'MESH':
        mesh = obj.data
        info['vertices'] = len(mesh.vertices)
        info['faces'] = len(mesh.polygons)
        info['edges'] = len(mesh.edges)
        info['modifiers'] = [
            {'name': m.name, 'type': m.type} for m in obj.modifiers
        ]
        info['materials'] = [
            m.name if m else None for m in mesh.materials
        ]
        # World-space bounding box
        bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        info['bbox_min'] = [round(min(v[i] for v in bb), 3) for i in range(3)]
        info['bbox_max'] = [round(max(v[i] for v in bb), 3) for i in range(3)]
        dims = [info['bbox_max'][i] - info['bbox_min'][i] for i in range(3)]
        info['dimensions_mm'] = [round(d, 2) for d in dims]
        # Non-manifold check (quick)
        bm = utils.get_bmesh(obj)
        non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
        info['non_manifold_edges'] = non_manifold
        info['is_watertight'] = non_manifold == 0
        bm.free()
    return info


def _ensure_scene_lighting():
    """Add default lighting if the scene has none. Prevents black renders."""
    has_light = any(obj.type == 'LIGHT' for obj in bpy.context.scene.objects)
    if not has_light:
        # Add a sun lamp
        bpy.ops.object.light_add(type='SUN', location=(0, 0, 10))
        sun = bpy.context.active_object
        sun.name = "_claude_sun"
        sun.data.energy = 3.0
        sun.data.angle = 0.5  # Soft shadows

    # Ensure world has ambient light
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("_claude_world")
        bpy.context.scene.world = world
    if world.use_nodes:
        bg = world.node_tree.nodes.get("Background")
        if bg and bg.inputs['Strength'].default_value < 0.1:
            bg.inputs['Strength'].default_value = 0.3
            bg.inputs['Color'].default_value = (0.05, 0.05, 0.08, 1.0)


def _ensure_mm_units():
    """Set scene units to millimeters. Always call at scene start to prevent scale bugs."""
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.length_unit = 'MILLIMETERS'
    scene.unit_settings.scale_length = 0.001


def handle_clear_scene(params):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    # Also clean orphan data
    for mesh in bpy.data.meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    # Always reset to mm units — prevents the scale_length=1.0 (meters) bug
    _ensure_mm_units()
    # Re-add default lighting so renders are never black
    _ensure_scene_lighting()
    return {'cleared': True, 'units': 'MILLIMETERS', 'scale_length': 0.001}


def handle_rename_object(params):
    obj = utils.resolve_object(params['old_name'])
    new_name = params['new_name']
    obj.name = new_name
    if obj.data:
        obj.data.name = new_name
    return {'old_name': params['old_name'], 'new_name': obj.name}


# ---------------------------------------------------------------------------
# Code execution
# ---------------------------------------------------------------------------

def handle_execute_code(params):
    code = params['code']
    # Auto-checkpoint before executing so Blender restart doesn't lose work
    try:
        utils.auto_save_checkpoint()
    except Exception:
        pass  # Never block execution over a save failure
    namespace = {
        'bpy': bpy, 'bmesh': bmesh, 'mathutils': __import__('mathutils'),
        'Vector': Vector, 'Matrix': __import__('mathutils').Matrix,
        'Euler': __import__('mathutils').Euler,
        'math': math, 'os': os,
        '__result__': None,
    }

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = captured_out = io.StringIO()
    sys.stderr = captured_err = io.StringIO()
    try:
        exec(code, namespace)
        stdout = captured_out.getvalue()
        stderr = captured_err.getvalue()
    except Exception:
        stdout = captured_out.getvalue()
        stderr = captured_err.getvalue() + traceback.format_exc()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    result = {
        'stdout': stdout,
        'stderr': stderr,
    }
    if namespace.get('__result__') is not None:
        result['result'] = str(namespace['__result__'])
    return result


# ---------------------------------------------------------------------------
# Render / visual feedback handlers
# ---------------------------------------------------------------------------

def handle_get_screenshot(params):
    _ensure_scene_lighting()
    elev = params.get('elevation', 35)
    azim = params.get('azimuth', 45)
    width = params.get('width', 800)
    height = params.get('height', 800)
    focus_object = params.get('focus_object')
    isolate = params.get('isolate', False)
    zoom = params.get('zoom', 1.0)

    # Focus on a specific object if requested
    target = None
    distance = None
    if focus_object:
        obj = utils.resolve_object(focus_object)
        target, radius = utils.get_object_bounds(obj)
        if radius < 0.001:
            radius = 1.0
        fov_rad = math.radians(50)
        distance = (radius / math.sin(fov_rad / 2) * 1.2) / zoom

    hidden = []
    if isolate and focus_object:
        hidden = utils.isolate_objects([focus_object])

    cam = utils.setup_render_camera(elev, azim, target=target, distance=distance)
    try:
        b64 = utils.render_to_base64(width, height, camera=cam)
    finally:
        cam_data = cam.data
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
        if hidden:
            utils.restore_visibility(hidden)

    return {'image': b64, 'format': 'png'}


def handle_render_tiled(params):
    _ensure_scene_lighting()
    angle_names = params.get('angles', ['iso', 'front', 'right', 'top'])
    tile_w = params.get('tile_width', 400)
    tile_h = params.get('tile_height', 400)
    focus_object = params.get('focus_object')
    isolate = params.get('isolate', False)
    zoom = params.get('zoom', 1.0)

    # Compute focus target/distance if specified
    target = None
    distance = None
    if focus_object:
        obj = utils.resolve_object(focus_object)
        target, radius = utils.get_object_bounds(obj)
        if radius < 0.001:
            radius = 1.0
        fov_rad = math.radians(50)
        distance = (radius / math.sin(fov_rad / 2) * 1.2) / zoom

    hidden = []
    if isolate and focus_object:
        hidden = utils.isolate_objects([focus_object])

    try:
        renders = []
        for name in angle_names:
            if name in utils.CAMERA_ANGLES:
                elev, azim = utils.CAMERA_ANGLES[name]
            else:
                raise ValueError(f"Unknown angle '{name}'. Available: {list(utils.CAMERA_ANGLES.keys())}")
            cam = utils.setup_render_camera(elev, azim, target=target, distance=distance)
            try:
                b64 = utils.render_to_base64(tile_w, tile_h, camera=cam)
            finally:
                cam_data = cam.data
                bpy.data.objects.remove(cam, do_unlink=True)
                bpy.data.cameras.remove(cam_data)
            renders.append({'label': name, 'image': b64})
    finally:
        if hidden:
            utils.restore_visibility(hidden)

    return {'renders': renders, 'format': 'png', 'tile_count': len(renders)}


def handle_render_turntable(params):
    """Render N angles around a specific object at a fixed elevation."""
    _ensure_scene_lighting()
    obj = utils.resolve_object(params['object_name'])
    steps = params.get('steps', 8)
    elevation = params.get('elevation', 20)
    tile_w = params.get('tile_width', 300)
    tile_h = params.get('tile_height', 300)
    isolate = params.get('isolate', False)
    zoom = params.get('zoom', 1.0)

    target, radius = utils.get_object_bounds(obj)
    if radius < 0.001:
        radius = 1.0
    fov_rad = math.radians(50)
    distance = (radius / math.sin(fov_rad / 2) * 1.2) / zoom

    hidden = []
    if isolate:
        hidden = utils.isolate_objects([obj.name])

    try:
        renders = []
        for i in range(steps):
            azimuth = (360.0 * i) / steps
            label = f"{int(azimuth)}°"
            cam = utils.setup_render_camera(elevation, azimuth, target=target, distance=distance)
            try:
                b64 = utils.render_to_base64(tile_w, tile_h, camera=cam)
            finally:
                cam_data = cam.data
                bpy.data.objects.remove(cam, do_unlink=True)
                bpy.data.cameras.remove(cam_data)
            renders.append({'label': label, 'image': b64})
    finally:
        if hidden:
            utils.restore_visibility(hidden)

    return {
        'renders': renders,
        'format': 'png',
        'tile_count': len(renders),
        'object': obj.name,
    }


def handle_cross_section(params):
    obj = utils.resolve_object(params['object_name'])
    utils.require_mesh(obj)
    axis = params.get('axis', 'z').upper()
    pct = params.get('percent', 50)
    width = params.get('width', 800)
    height = params.get('height', 800)

    if axis not in ('X', 'Y', 'Z'):
        raise ValueError(f"Invalid axis '{axis}'. Use X, Y, or Z.")
    if not (0 <= pct <= 100):
        raise ValueError(f"Percent must be 0-100, got {pct}")

    utils.auto_save_checkpoint()

    axis_idx = {'X': 0, 'Y': 1, 'Z': 2}[axis]
    bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    min_val = min(v[axis_idx] for v in bb)
    max_val = max(v[axis_idx] for v in bb)
    cut_pos = min_val + (max_val - min_val) * (pct / 100)

    # Duplicate the object
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.duplicate()
    dup = bpy.context.active_object
    dup.name = "_claude_cross_section_tmp"

    # Create cutting box
    bpy.ops.mesh.primitive_cube_add(size=1)
    cutter = bpy.context.active_object
    cutter.name = "_claude_cutter_tmp"

    # Size and place the cutter so its near face sits AT cut_pos and it
    # extends past max_val on the cut axis, with full bbox coverage on the
    # other two axes. Cube primitive is size=1, so world extent == scale.
    bb_min = [min(v[i] for v in bb) for i in range(3)]
    bb_max = [max(v[i] for v in bb) for i in range(3)]
    buf = 10.0
    sc = [0.0, 0.0, 0.0]
    lc = [0.0, 0.0, 0.0]
    for i in range(3):
        if i == axis_idx:
            sc[i] = max((bb_max[i] - cut_pos) + buf, 0.1)
            lc[i] = cut_pos + sc[i] / 2
        else:
            sc[i] = (bb_max[i] - bb_min[i]) + buf
            lc[i] = (bb_max[i] + bb_min[i]) / 2
    cutter.scale = sc
    cutter.location = Vector(lc)
    center = (Vector(bb_min) + Vector(bb_max)) / 2

    bpy.context.view_layer.update()

    # Boolean difference
    bool_mod = dup.modifiers.new(name="CrossSection", type='BOOLEAN')
    bool_mod.operation = 'DIFFERENCE'
    bool_mod.object = cutter
    bool_mod.solver = 'EXACT'

    bpy.context.view_layer.objects.active = dup
    bpy.ops.object.modifier_apply(modifier="CrossSection")

    # Hide original and cutter for render
    obj.hide_render = True
    cutter.hide_render = True

    # Camera looks at the cut face
    cam_angles = {
        'X': (0, 90 if pct <= 50 else -90),
        'Y': (0, 0 if pct <= 50 else 180),
        'Z': (90 if pct <= 50 else -90, 0),
    }
    elev, azim = cam_angles[axis]
    cam = utils.setup_render_camera(elev, azim, target=Vector([
        center[0] if axis != 'X' else cut_pos,
        center[1] if axis != 'Y' else cut_pos,
        center[2] if axis != 'Z' else cut_pos,
    ]))

    try:
        b64 = utils.render_to_base64(width, height, camera=cam)
    finally:
        # Cleanup
        cam_data = cam.data
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
        obj.hide_render = False
        utils.cleanup_temp_object(cutter)
        utils.cleanup_temp_object(dup)

    return {
        'image': b64, 'format': 'png',
        'axis': axis, 'percent': pct,
        'cut_position_mm': round(cut_pos, 2),
    }


def handle_cross_section_gallery(params):
    obj_name = params['object_name']
    axes = params.get('axes', ['x', 'y', 'z'])
    percents = params.get('percents', [10, 30, 50, 70, 90])
    tile_w = params.get('tile_width', 300)
    tile_h = params.get('tile_height', 300)

    renders = []
    for axis in axes:
        for pct in percents:
            result = handle_cross_section({
                'object_name': obj_name,
                'axis': axis,
                'percent': pct,
                'width': tile_w,
                'height': tile_h,
            })
            renders.append({
                'label': f"{axis.upper()} {pct}%",
                'image': result['image'],
            })

    return {'renders': renders, 'format': 'png', 'tile_count': len(renders)}


def handle_render_printability_heatmap(params):
    """Run printability check, apply vertex colors, render tiled."""
    obj = utils.resolve_object(params['object_name'])
    utils.require_mesh(obj)
    angle_threshold = params.get('overhang_angle', 45.0)
    min_wall = params.get('min_wall_mm', 0.8)

    # Run checks
    overhang_data = _compute_overhangs(obj, angle_threshold)
    thin_wall_data = _compute_thin_walls(obj, min_wall)

    # Build face color map
    face_colors = {}
    for idx in overhang_data['face_indices']:
        face_colors[idx] = (1.0, 0.2, 0.0, 1.0)  # Red for overhangs
    for idx, thickness in thin_wall_data['faces']:
        face_colors[idx] = (1.0, 0.9, 0.0, 1.0)  # Yellow for thin walls
    # Overhangs take priority if both

    # Apply vertex colors to the actual mesh
    utils.set_vertex_colors(obj, face_colors, "PrintabilityHeatmap")

    # Set material to show vertex colors
    _ensure_vertex_color_material(obj, "PrintabilityHeatmap")

    # Render from multiple angles
    angles = params.get('angles', ['iso', 'front', 'right', 'top'])
    renders = []
    for name in angles:
        elev, azim = utils.CAMERA_ANGLES.get(name, (35, 45))
        b64, label = utils.render_angle(name, elev, azim)
        renders.append({'label': label, 'image': b64})

    # Remove the vertex color material (restore original appearance)
    _remove_vertex_color_material(obj)

    return {
        'renders': renders,
        'format': 'png',
        'overhang_faces': overhang_data['count'],
        'thin_wall_faces': thin_wall_data['count'],
        'summary': (
            f"{overhang_data['count']} overhang faces (>{angle_threshold}deg), "
            f"{thin_wall_data['count']} thin wall faces (<{min_wall}mm)"
        ),
    }


def _ensure_vertex_color_material(obj, layer_name):
    """Create a temporary material that displays vertex colors."""
    mat = bpy.data.materials.new(name="_claude_vcol_tmp")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    # Vertex color node -> emission -> output
    vcol_node = nodes.new('ShaderNodeVertexColor')
    vcol_node.layer_name = layer_name

    emission = nodes.new('ShaderNodeEmission')
    output = nodes.new('ShaderNodeOutputMaterial')

    links.new(vcol_node.outputs['Color'], emission.inputs['Color'])
    links.new(emission.outputs['Emission'], output.inputs['Surface'])

    # Store original materials and replace
    obj['_claude_orig_materials'] = [
        m.name if m else '' for m in obj.data.materials
    ]
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _remove_vertex_color_material(obj):
    """Restore original materials after heatmap render."""
    # Remove temp material
    if obj.data.materials and obj.data.materials[0] and obj.data.materials[0].name == "_claude_vcol_tmp":
        mat = obj.data.materials[0]
        obj.data.materials.clear()
        bpy.data.materials.remove(mat)

    # Restore originals
    orig_names = obj.get('_claude_orig_materials', [])
    for name in orig_names:
        if name:
            mat = bpy.data.materials.get(name)
            if mat:
                obj.data.materials.append(mat)
    if '_claude_orig_materials' in obj:
        del obj['_claude_orig_materials']

    # Remove vertex color layer
    if "PrintabilityHeatmap" in obj.data.color_attributes:
        obj.data.color_attributes.remove(obj.data.color_attributes["PrintabilityHeatmap"])


def handle_render_with_dimensions(params):
    """Render with bounding box dimensions overlaid.

    Returns the render plus dimension data (actual annotation done server-side with PIL).
    """
    obj_names = params.get('object_names', [])
    if not obj_names:
        obj_names = [o.name for o in bpy.context.scene.objects if o.type == 'MESH']

    measurements = []
    for name in obj_names:
        obj = utils.resolve_object(name)
        if obj.type != 'MESH':
            continue
        bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        dims = [
            max(v[i] for v in bb) - min(v[i] for v in bb) for i in range(3)
        ]
        measurements.append({
            'name': name,
            'dimensions_mm': [round(d, 2) for d in dims],
            'bbox_min': [round(min(v[i] for v in bb), 2) for i in range(3)],
            'bbox_max': [round(max(v[i] for v in bb), 2) for i in range(3)],
        })

    # Render iso view
    b64, _ = utils.render_angle('iso', 35, 45, 800, 800)

    return {
        'image': b64, 'format': 'png',
        'measurements': measurements,
    }


def handle_render_before_after(params):
    """Capture before state, execute code, capture after state."""
    code = params['code']
    width = params.get('width', 400)
    height = params.get('height', 400)

    # Before render
    before_cam = utils.setup_render_camera(35, 45)
    before_b64 = utils.render_to_base64(width, height, camera=before_cam)
    before_data = before_cam.data
    bpy.data.objects.remove(before_cam, do_unlink=True)
    bpy.data.cameras.remove(before_data)

    # Execute the code
    code_result = handle_execute_code({'code': code})

    # After render (same angle)
    after_cam = utils.setup_render_camera(35, 45)
    after_b64 = utils.render_to_base64(width, height, camera=after_cam)
    after_data = after_cam.data
    bpy.data.objects.remove(after_cam, do_unlink=True)
    bpy.data.cameras.remove(after_data)

    return {
        'before_image': before_b64,
        'after_image': after_b64,
        'format': 'png',
        'code_output': code_result,
    }


# ---------------------------------------------------------------------------
# Print validation handlers
# ---------------------------------------------------------------------------

def _compute_overhangs(obj, angle_threshold_deg=45.0):
    """Core overhang computation. Returns dict with face data."""
    analysis_obj, was_decimated = utils.auto_decimate(obj)
    try:
        bm = utils.get_bmesh(analysis_obj)
        bm.faces.ensure_lookup_table()

        z_threshold = -math.sin(math.radians(angle_threshold_deg))
        overhangs = []
        worst_angle = 0
        worst_z = None

        for face in bm.faces:
            if face.normal.z < z_threshold:
                # Skip build-plate faces (bottom-facing at z ~ 0)
                center = face.calc_center_median()
                if center.z < 0.1 and face.normal.z < -0.99:
                    continue
                # Angle from horizontal (FDM convention: 0=flat on bed, 90=vertical wall)
                # face.normal.z = -sin(angle_from_horizontal) for downward faces
                angle = math.degrees(math.asin(max(-1, min(1, -face.normal.z))))
                overhangs.append(face.index)
                if angle > worst_angle:
                    worst_angle = angle
                    worst_z = round(center.z, 2)

        bm.free()
    finally:
        if was_decimated:
            utils.cleanup_temp_object(analysis_obj)

    return {
        'count': len(overhangs),
        'face_indices': overhangs,
        'worst_angle_from_horizontal_deg': round(worst_angle, 1),
        'worst_z_mm': worst_z,
        'threshold_deg': angle_threshold_deg,
        'approximate': was_decimated,
    }


def handle_check_overhangs(params):
    obj = utils.resolve_object(params['object_name'])
    utils.require_mesh(obj)
    angle = params.get('angle_threshold', 45.0)
    result = _compute_overhangs(obj, angle)
    result['summary'] = (
        f"{result['count']} overhang faces (>{angle}deg from horizontal)"
        + (f", worst: {result['worst_angle_from_horizontal_deg']}deg at Z={result['worst_z_mm']}mm"
           if result['count'] > 0 else "")
    )
    return result


def _compute_thin_walls(obj, min_thickness_mm=0.8):
    """Core thin wall computation via raycasting."""
    # Guard: if scene units are not mm, results will be meaningless
    scale = bpy.context.scene.unit_settings.scale_length
    scale_warning = None
    if abs(scale - 0.001) > 1e-6:
        scale_warning = f"scale_length={scale} (not mm) — results are in wrong units. Run blender_clear_scene to fix."

    analysis_obj, was_decimated = utils.auto_decimate(obj)
    try:
        bm = utils.get_bmesh(analysis_obj)
        bm.faces.ensure_lookup_table()
        bvh = BVHTree.FromBMesh(bm)

        thin_faces = []
        min_found = float('inf')

        # Compute approximate object center for inward-face detection
        obj_center = Vector((
            sum(v.co.x for v in bm.verts) / len(bm.verts),
            sum(v.co.y for v in bm.verts) / len(bm.verts),
            sum(v.co.z for v in bm.verts) / len(bm.verts),
        ))

        for face in bm.faces:
            origin = face.calc_center_median()

            # Skip inward-facing cavity surfaces — their normals point toward the
            # object center. These are interior boolean seam faces, not structural walls.
            to_center = (obj_center - origin)
            if to_center.length > 0 and face.normal.dot(to_center.normalized()) > 0.5:
                continue

            direction = -face.normal
            origin_offset = origin + direction * 0.001

            hit_loc, hit_norm, hit_idx, hit_dist = bvh.ray_cast(origin_offset, direction)
            if hit_loc is not None and hit_dist < min_thickness_mm:
                thin_faces.append((face.index, round(hit_dist, 3)))
                if hit_dist < min_found:
                    min_found = hit_dist

        bm.free()
    finally:
        if was_decimated:
            utils.cleanup_temp_object(analysis_obj)

    result = {
        'count': len(thin_faces),
        'faces': thin_faces,
        'min_thickness_mm': round(min_found, 3) if thin_faces else None,
        'threshold_mm': min_thickness_mm,
        'approximate': was_decimated,
    }
    if scale_warning:
        result['scale_warning'] = scale_warning
    return result


def handle_check_thin_walls(params):
    obj = utils.resolve_object(params['object_name'])
    utils.require_mesh(obj)
    min_wall = params.get('min_thickness_mm', 0.8)
    result = _compute_thin_walls(obj, min_wall)
    result['summary'] = (
        f"{result['count']} thin wall faces (<{min_wall}mm)"
        + (f", thinnest: {result['min_thickness_mm']}mm"
           if result['count'] > 0 else "")
    )
    return result


def handle_check_clearance(params):
    obj_a = utils.resolve_object(params['object_a'])
    obj_b = utils.resolve_object(params['object_b'])
    utils.require_mesh(obj_a)
    utils.require_mesh(obj_b)
    min_clearance = params.get('min_clearance_mm', 0.3)

    if obj_a.name == obj_b.name:
        raise ValueError("Cannot check clearance of an object against itself")

    bm_a = utils.get_bmesh(obj_a)
    bm_b = utils.get_bmesh(obj_b)
    bvh_b = BVHTree.FromBMesh(bm_b)

    min_dist = float('inf')
    closest_a = closest_b = None
    too_close_count = 0

    bm_a.faces.ensure_lookup_table()
    for face in bm_a.faces:
        pt = face.calc_center_median()
        nearest_loc, _, _, _ = bvh_b.find_nearest(pt)
        if nearest_loc is not None:
            dist = (pt - nearest_loc).length
            if dist < min_dist:
                min_dist = dist
                closest_a = [round(x, 3) for x in pt]
                closest_b = [round(x, 3) for x in nearest_loc]
            if dist < min_clearance:
                too_close_count += 1

    # Also check vertices for precision
    for vert in bm_a.verts:
        nearest_loc, _, _, _ = bvh_b.find_nearest(vert.co)
        if nearest_loc is not None:
            dist = (vert.co - nearest_loc).length
            if dist < min_dist:
                min_dist = dist
                closest_a = [round(x, 3) for x in vert.co]
                closest_b = [round(x, 3) for x in nearest_loc]

    bm_a.free()
    bm_b.free()

    is_ok = min_dist >= min_clearance
    return {
        'min_distance_mm': round(min_dist, 3),
        'closest_point_a': closest_a,
        'closest_point_b': closest_b,
        'faces_too_close': too_close_count,
        'is_ok': is_ok,
        'threshold_mm': min_clearance,
        'summary': (
            f"Clearance {obj_a.name}<->{obj_b.name}: "
            f"{round(min_dist, 3)}mm ({'OK' if is_ok else 'TOO CLOSE'})"
        ),
    }


def handle_check_clearance_sweep(params):
    """Rotate an inner object around an axis and check clearance at each step."""
    inner = utils.resolve_object(params['inner_object'])
    outer = utils.resolve_object(params['outer_object'])
    utils.require_mesh(inner)
    utils.require_mesh(outer)
    axis = params.get('axis', 'Z').upper()
    steps = params.get('steps', 36)
    min_clearance = params.get('min_clearance_mm', 0.3)

    axis_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(axis)
    if axis_idx is None:
        raise ValueError(f"Invalid axis '{axis}'. Use X, Y, or Z.")

    orig_rot = list(inner.rotation_euler)
    worst_dist = float('inf')
    worst_angle = 0
    all_ok = True
    results_per_step = []

    for step in range(steps):
        angle_deg = (360.0 * step) / steps
        angle_rad = math.radians(angle_deg)
        inner.rotation_euler[axis_idx] = orig_rot[axis_idx] + angle_rad
        bpy.context.view_layer.update()

        # Quick clearance check (faces only, no verts for speed)
        bm_i = utils.get_bmesh(inner)
        bm_o = utils.get_bmesh(outer)
        bvh_o = BVHTree.FromBMesh(bm_o)

        min_dist = float('inf')
        bm_i.faces.ensure_lookup_table()
        for face in bm_i.faces:
            pt = face.calc_center_median()
            nearest_loc, _, _, _ = bvh_o.find_nearest(pt)
            if nearest_loc is not None:
                dist = (pt - nearest_loc).length
                if dist < min_dist:
                    min_dist = dist

        bm_i.free()
        bm_o.free()

        step_ok = min_dist >= min_clearance
        if not step_ok:
            all_ok = False
        if min_dist < worst_dist:
            worst_dist = min_dist
            worst_angle = angle_deg

        results_per_step.append({
            'angle_deg': round(angle_deg, 1),
            'min_distance_mm': round(min_dist, 3),
            'is_ok': step_ok,
        })

    # Restore rotation
    inner.rotation_euler = orig_rot
    bpy.context.view_layer.update()

    return {
        'passes': all_ok,
        'worst_distance_mm': round(worst_dist, 3),
        'worst_angle_deg': round(worst_angle, 1),
        'steps': results_per_step,
        'threshold_mm': min_clearance,
        'summary': (
            f"Sweep {inner.name} in {outer.name} around {axis}: "
            f"{'PASS' if all_ok else 'FAIL'}, "
            f"worst={round(worst_dist, 3)}mm at {round(worst_angle, 1)}deg"
        ),
    }


def handle_full_printability_check(params):
    obj = utils.resolve_object(params['object_name'])
    utils.require_mesh(obj)
    overhang_angle = params.get('overhang_angle', 45.0)
    min_wall = params.get('min_wall_mm', 0.8)
    clearance_partners = params.get('clearance_partners', [])
    min_clearance = params.get('min_clearance_mm', 0.3)

    results = {}

    # Overhangs
    results['overhangs'] = _compute_overhangs(obj, overhang_angle)

    # Thin walls
    results['thin_walls'] = _compute_thin_walls(obj, min_wall)

    # Non-manifold & degenerate
    bm = utils.get_bmesh(obj)
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    degenerate = sum(1 for f in bm.faces if f.calc_area() < 1e-6)
    results['mesh_health'] = {
        'non_manifold_edges': non_manifold,
        'degenerate_faces': degenerate,
        'is_watertight': non_manifold == 0,
        'total_faces': len(bm.faces),
        'total_vertices': len(bm.verts),
    }

    # Volume (only meaningful if watertight)
    if non_manifold == 0:
        vol = bm.calc_volume()
        results['mesh_health']['volume_mm3'] = round(abs(vol), 2)

    bm.free()

    # Clearance checks
    if clearance_partners:
        results['clearance'] = {}
        for partner_name in clearance_partners:
            partner = utils.resolve_object(partner_name)
            results['clearance'][partner_name] = handle_check_clearance({
                'object_a': obj.name,
                'object_b': partner_name,
                'min_clearance_mm': min_clearance,
            })

    # Overall pass/fail
    passes = (
        results['overhangs']['count'] == 0
        and results['thin_walls']['count'] == 0
        and non_manifold == 0
        and degenerate == 0
    )
    if clearance_partners:
        passes = passes and all(
            c['is_ok'] for c in results['clearance'].values()
        )

    results['passes'] = passes
    results['summary'] = (
        f"{'PASS' if passes else 'ISSUES FOUND'}: "
        f"{results['overhangs']['count']} overhangs, "
        f"{results['thin_walls']['count']} thin walls, "
        f"{non_manifold} non-manifold edges, "
        f"{degenerate} degenerate faces"
    )

    return results


# ---------------------------------------------------------------------------
# Export / import handlers
# ---------------------------------------------------------------------------

def handle_export_stl(params):
    path = params['path']
    obj_name = params.get('object_name')       # Single object
    obj_names = params.get('object_names')      # Multiple objects (bundled)
    binary = params.get('binary', True)

    # Select the target object(s)
    bpy.ops.object.select_all(action='DESELECT')

    selected = []
    if obj_names:
        # Export specific objects bundled into one STL
        for name in obj_names:
            obj = utils.resolve_object(name)
            utils.require_mesh(obj)
            obj.select_set(True)
            selected.append(obj)
        bpy.context.view_layer.objects.active = selected[0]
    elif obj_name:
        # Export single object
        obj = utils.resolve_object(obj_name)
        utils.require_mesh(obj)
        obj.select_set(True)
        selected.append(obj)
        bpy.context.view_layer.objects.active = obj
    else:
        # Export ALL mesh objects as one STL
        for o in bpy.context.scene.objects:
            if o.type == 'MESH' and not o.name.startswith("_claude_"):
                o.select_set(True)
                selected.append(o)
        if selected:
            bpy.context.view_layer.objects.active = selected[0]

    if not selected:
        raise ValueError("No mesh objects to export")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    bpy.ops.wm.stl_export(
        filepath=os.path.abspath(path),
        export_selected_objects=True,
        ascii_format=not binary,
    )

    size = os.path.getsize(path)

    # Check for non-manifold warnings on all exported objects
    warnings = []
    for obj in selected:
        bm = utils.get_bmesh(obj)
        nm = sum(1 for e in bm.edges if not e.is_manifold)
        if nm > 0:
            warnings.append(f"WARNING: {obj.name} has {nm} non-manifold edges")
        bm.free()

    return {
        'path': os.path.abspath(path),
        'size_bytes': size,
        'objects_exported': [o.name for o in selected],
        'warnings': warnings,
    }


def handle_import_stl(params):
    path = params['path']
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    bpy.ops.wm.stl_import(filepath=os.path.abspath(path))

    imported = bpy.context.active_object
    name = imported.name if imported else "unknown"
    verts = len(imported.data.vertices) if imported else 0
    faces = len(imported.data.polygons) if imported else 0

    return {
        'object_name': name,
        'vertices': verts,
        'faces': faces,
        'path': os.path.abspath(path),
    }


def handle_save_blend(params):
    path = params.get('path')
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(path))
        return {'path': os.path.abspath(path)}
    elif bpy.data.filepath:
        bpy.ops.wm.save_mainfile()
        return {'path': bpy.data.filepath}
    else:
        path = os.path.join(tempfile.gettempdir(), 'printable_blender_scene.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return {'path': path}


# ---------------------------------------------------------------------------
# Boolean operation (typed tool — handles context, ordering, connectivity)
# ---------------------------------------------------------------------------

def handle_boolean(params):
    """Perform a boolean operation robustly: apply in correct order, verify result."""
    target_name = params['target']
    cutter_name = params['cutter']
    operation = params.get('operation', 'DIFFERENCE').upper()
    keep_cutter = params.get('keep_cutter', False)
    solver = params.get('solver', 'EXACT')

    if operation not in ('DIFFERENCE', 'UNION', 'INTERSECT'):
        raise ValueError(f"Invalid operation '{operation}'. Use DIFFERENCE, UNION, or INTERSECT.")

    target = utils.resolve_object(target_name)
    cutter = utils.resolve_object(cutter_name)
    utils.require_mesh(target)
    utils.require_mesh(cutter)

    utils.auto_save_checkpoint()

    face_count_before = len(target.data.polygons)

    # Ensure object mode and correct active object
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    target.select_set(True)
    bpy.context.view_layer.objects.active = target

    # Apply any existing modifiers on target first to avoid ordering issues
    for mod in list(target.modifiers):
        if mod.type == 'BOOLEAN':
            try:
                bpy.ops.object.modifier_apply(modifier=mod.name)
            except Exception:
                target.modifiers.remove(mod)

    # Add and apply the new boolean
    mod = target.modifiers.new("_claude_bool", 'BOOLEAN')
    mod.operation = operation
    mod.object = cutter
    mod.solver = solver
    bpy.ops.object.modifier_apply(modifier="_claude_bool")

    if not keep_cutter:
        cutter_mesh = cutter.data
        bpy.data.objects.remove(cutter, do_unlink=True)
        if cutter_mesh.users == 0:
            bpy.data.meshes.remove(cutter_mesh)

    # Connectivity check
    bm = utils.get_bmesh(target)
    bm.verts.ensure_lookup_table()
    visited = set()
    components = 0
    for v in bm.verts:
        if v.index in visited:
            continue
        components += 1
        stack = [v]
        while stack:
            cur = stack.pop()
            if cur.index in visited:
                continue
            visited.add(cur.index)
            for edge in cur.link_edges:
                for other in edge.verts:
                    if other.index not in visited:
                        stack.append(other)
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    face_count_after = len(bm.faces)
    bm.free()

    warnings = []
    if components > 1:
        warnings.append(f"DISCONNECTED: {components} islands — boolean union on coplanar faces. Increase overlap.")
    if non_manifold > 0:
        warnings.append(f"NON-MANIFOLD: {non_manifold} edges — boolean may have failed.")
    if face_count_after == face_count_before and operation == 'DIFFERENCE':
        warnings.append("Face count unchanged — cutter may not have overlapped target.")

    return {
        'target': target_name,
        'operation': operation,
        'face_count_before': face_count_before,
        'face_count_after': face_count_after,
        'connected_components': components,
        'non_manifold_edges': non_manifold,
        'warnings': warnings,
        'ok': len(warnings) == 0,
        'summary': (
            f"{operation} on {target_name}: {face_count_before}→{face_count_after} faces, "
            f"{components} component(s)"
            + (f" ⚠ {'; '.join(warnings)}" if warnings else " ✓")
        ),
    }


# ---------------------------------------------------------------------------
# Mesh health (lightweight checkpoint tool)
# ---------------------------------------------------------------------------

def handle_mesh_health(params):
    """Fast mesh stats — designed to be called after each boolean as a checkpoint."""
    obj = utils.resolve_object(params['object_name'])
    utils.require_mesh(obj)

    bm = utils.get_bmesh(obj)
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    degenerate = sum(1 for f in bm.faces if f.calc_area() < 1e-6)
    is_watertight = non_manifold == 0

    # Connected components — flood-fill via vertex adjacency
    # Disconnected islands = boolean union on coplanar faces (zero-volume bond)
    visited = set()
    components = 0
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        if v.index in visited:
            continue
        components += 1
        stack = [v]
        while stack:
            cur = stack.pop()
            if cur.index in visited:
                continue
            visited.add(cur.index)
            for edge in cur.link_edges:
                for other in edge.verts:
                    if other.index not in visited:
                        stack.append(other)

    is_connected = components == 1

    result = {
        'name': obj.name,
        'vertices': len(bm.verts),
        'faces': len(bm.faces),
        'edges': len(bm.edges),
        'non_manifold_edges': non_manifold,
        'degenerate_faces': degenerate,
        'is_watertight': is_watertight,
        'connected_components': components,
        'is_connected': is_connected,
    }

    if is_watertight:
        result['volume_mm3'] = round(abs(bm.calc_volume()), 2)

    bm.free()

    # Bounding box
    bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    dims = [max(v[i] for v in bb) - min(v[i] for v in bb) for i in range(3)]
    result['dimensions_mm'] = [round(d, 2) for d in dims]

    issues = []
    if non_manifold > 0:
        issues.append(f"{non_manifold} non-manifold edges")
    if degenerate > 0:
        issues.append(f"{degenerate} degenerate faces")
    if not is_connected:
        issues.append(f"DISCONNECTED ({components} islands)")
    status = "ISSUES" if issues else "OK"
    result['summary'] = (
        f"{obj.name}: {result['faces']} faces, "
        f"{'watertight' if is_watertight else 'NOT watertight'}, "
        f"{components} component{'s' if components > 1 else ''}, "
        f"{dims[0]:.1f}x{dims[1]:.1f}x{dims[2]:.1f}mm [{status}]"
    )

    return result


# ---------------------------------------------------------------------------
# Intersection check (geometry overlap detection)
# ---------------------------------------------------------------------------

def handle_check_intersection(params):
    """Check if two objects' meshes overlap (geometry intersects).

    Distinct from clearance — clearance measures distance, this detects
    actual mesh overlap which indicates a boolean went wrong or parts are fused.
    """
    obj_a = utils.resolve_object(params['object_a'])
    obj_b = utils.resolve_object(params['object_b'])
    utils.require_mesh(obj_a)
    utils.require_mesh(obj_b)

    if obj_a.name == obj_b.name:
        raise ValueError("Cannot check intersection of an object with itself")

    bm_a = utils.get_bmesh(obj_a)
    bm_b = utils.get_bmesh(obj_b)

    bvh_a = BVHTree.FromBMesh(bm_a)
    bvh_b = BVHTree.FromBMesh(bm_b)

    overlap_pairs = bvh_a.overlap(bvh_b)

    bm_a.free()
    bm_b.free()

    intersects = len(overlap_pairs) > 0
    return {
        'intersects': intersects,
        'overlap_face_pairs': len(overlap_pairs),
        'summary': (
            f"{obj_a.name} {'INTERSECTS' if intersects else 'does not intersect'} "
            f"{obj_b.name}"
            + (f" ({len(overlap_pairs)} overlapping face pairs)" if intersects else "")
        ),
    }


# ---------------------------------------------------------------------------
# Retention check (captive geometry validation)
# ---------------------------------------------------------------------------

def handle_check_retention(params):
    """Check if moving_object is captive against static_objects in a given direction."""
    import mathutils

    moving_name = params.get('moving_object')
    static_names = params.get('static_objects', [])
    direction = params.get('direction', '+Z')
    displacement = float(params.get('displacement', 20.0))

    moving_obj = bpy.data.objects.get(moving_name)
    if not moving_obj:
        return {'error': f'Object not found: {moving_name}'}

    static_objs = []
    for name in static_names:
        obj = bpy.data.objects.get(name)
        if obj:
            static_objs.append(obj)

    if not static_objs:
        return {'error': 'No valid static objects found'}

    # Parse direction
    axis_map = {'+X': (1, 0, 0), '-X': (-1, 0, 0), '+Y': (0, 1, 0),
                '-Y': (0, -1, 0), '+Z': (0, 0, 1), '-Z': (0, 0, -1)}
    vec = axis_map.get(direction.upper())
    if not vec:
        return {'error': f'Invalid direction: {direction}. Use +X/-X/+Y/-Y/+Z/-Z'}

    # Save original location
    original_loc = moving_obj.location.copy()

    # Displace
    offset = mathutils.Vector(vec) * displacement
    moving_obj.location = original_loc + offset
    bpy.context.view_layer.update()

    # Check intersection against each static object
    results = []
    any_captive = False

    for static_obj in static_objs:
        try:
            bm_moving = utils.get_bmesh(moving_obj)
            bm_static = utils.get_bmesh(static_obj)

            bvh_moving = BVHTree.FromBMesh(bm_moving)
            bvh_static = BVHTree.FromBMesh(bm_static)

            overlap = bvh_moving.overlap(bvh_static)

            bm_moving.free()
            bm_static.free()

            intersects = len(overlap) > 0
            if intersects:
                any_captive = True

            results.append({
                'static_object': static_obj.name,
                'intersects_at_displacement': intersects,
                'overlap_pairs': len(overlap),
            })
        except Exception as e:
            results.append({'static_object': static_obj.name, 'error': str(e)})

    # Restore location
    moving_obj.location = original_loc
    bpy.context.view_layer.update()

    verdict = 'CAPTIVE' if any_captive else 'FREE'

    return {
        'moving_object': moving_name,
        'direction': direction,
        'displacement_mm': displacement,
        'verdict': verdict,
        'description': (
            f'{moving_name} is blocked by geometry when moved {displacement}mm in {direction} direction — it is captive.'
            if any_captive else
            f'{moving_name} moves freely {displacement}mm in {direction} direction with no blocking geometry — retention mechanism is BROKEN.'
        ),
        'details': results,
    }


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

HANDLERS = {
    # Scene
    'get_scene_info': handle_get_scene_info,
    'get_object_info': handle_get_object_info,
    'clear_scene': handle_clear_scene,
    'rename_object': handle_rename_object,
    # Code
    'execute_code': handle_execute_code,
    # Render / visual
    'get_screenshot': handle_get_screenshot,
    'render_tiled': handle_render_tiled,
    'render_turntable': handle_render_turntable,
    'cross_section': handle_cross_section,
    'cross_section_gallery': handle_cross_section_gallery,
    'render_printability_heatmap': handle_render_printability_heatmap,
    'render_with_dimensions': handle_render_with_dimensions,
    'render_before_after': handle_render_before_after,
    # Print validation
    'check_overhangs': handle_check_overhangs,
    'check_thin_walls': handle_check_thin_walls,
    'check_clearance': handle_check_clearance,
    'check_clearance_sweep': handle_check_clearance_sweep,
    'full_printability_check': handle_full_printability_check,
    # Boolean operation
    'boolean': handle_boolean,
    # Mesh health & intersection
    'mesh_health': handle_mesh_health,
    'check_intersection': handle_check_intersection,
    'check_retention': handle_check_retention,
    # Export
    'export_stl': handle_export_stl,
    'import_stl': handle_import_stl,
    'save_blend': handle_save_blend,
}
