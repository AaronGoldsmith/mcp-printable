"""Shared utilities for the Printable Blender addon."""

import bpy
import bmesh
import math
import tempfile
import base64
import os
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def get_bmesh(obj, apply_modifiers=True):
    """Get a BMesh from a mesh object with world transform applied.

    If apply_modifiers is True, evaluates the depsgraph so modifiers
    (booleans, mirrors, etc.) are baked into the mesh before analysis.
    Always call bm.free() when done.
    """
    bm = bmesh.new()
    if apply_modifiers:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        obj_eval = obj.evaluated_get(depsgraph)
        bm.from_mesh(obj_eval.to_mesh())
        obj_eval.to_mesh_clear()
    else:
        bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    bm.normal_update()
    return bm


def resolve_object(name):
    """Resolve an object by name, raising KeyError with available names if not found."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        available = [o.name for o in bpy.context.scene.objects]
        raise KeyError(f"Object '{name}' not found. Available: {available}")
    return obj


def require_mesh(obj):
    """Ensure an object is a mesh, raising TypeError otherwise."""
    if obj.type != 'MESH':
        raise TypeError(f"Object '{obj.name}' is type '{obj.type}', not MESH")
    return obj


def get_scene_bounds():
    """Compute the bounding box encompassing all mesh objects in the scene."""
    min_co = Vector((float('inf'),) * 3)
    max_co = Vector((float('-inf'),) * 3)
    found = False
    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH':
            continue
        found = True
        for corner in obj.bound_box:
            world_co = obj.matrix_world @ Vector(corner)
            for i in range(3):
                if world_co[i] < min_co[i]:
                    min_co[i] = world_co[i]
                if world_co[i] > max_co[i]:
                    max_co[i] = world_co[i]
    if not found:
        return Vector((0, 0, 0)), Vector((0, 0, 0))
    return min_co, max_co


def auto_decimate(obj, target_faces=50000):
    """Add and apply a decimate modifier if face count exceeds target.

    Operates on a DUPLICATE so the original is untouched.
    Returns (decimated_obj, was_decimated).
    """
    if len(obj.data.polygons) <= target_faces:
        return obj, False

    # Duplicate
    new_mesh = obj.data.copy()
    new_obj = obj.copy()
    new_obj.data = new_mesh
    new_obj.name = obj.name + "_analysis_tmp"
    bpy.context.collection.objects.link(new_obj)

    ratio = target_faces / len(obj.data.polygons)
    mod = new_obj.modifiers.new(name="AutoDecimate", type='DECIMATE')
    mod.ratio = ratio

    bpy.context.view_layer.objects.active = new_obj
    bpy.ops.object.modifier_apply(modifier="AutoDecimate")

    return new_obj, True


def cleanup_temp_object(obj):
    """Remove a temporary object and its mesh data."""
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def get_object_bounds(obj):
    """Compute bounding box center and radius for a single object (world space)."""
    bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    min_co = Vector((min(v[i] for v in bb) for i in range(3)))
    max_co = Vector((max(v[i] for v in bb) for i in range(3)))
    center = (min_co + max_co) / 2
    radius = (max_co - min_co).length / 2
    return center, radius


def isolate_objects(names):
    """Hide all objects except those in names. Returns list of objects that were hidden."""
    hidden = []
    for obj in bpy.context.scene.objects:
        if obj.name not in names and not obj.name.startswith("_agent_") and not obj.name.startswith("_claude_"):
            if obj.visible_get():
                obj.hide_render = True
                hidden.append(obj)
    return hidden


def restore_visibility(hidden_objects):
    """Restore visibility for objects hidden by isolate_objects."""
    for obj in hidden_objects:
        obj.hide_render = False


# Predefined camera angles: (elevation_deg, azimuth_deg)
CAMERA_ANGLES = {
    'iso':    (35,  45),
    'front':  (0,   0),
    'back':   (0,   180),
    'right':  (0,   90),
    'left':   (0,   -90),
    'top':    (90,  0),
}


def setup_render_camera(elevation_deg, azimuth_deg, target=None, distance=None):
    """Create a temp camera positioned at the given angles, looking at target.

    Returns the camera object. Caller should delete it when done.
    """
    if target is None or distance is None:
        min_co, max_co = get_scene_bounds()
        if target is None:
            target = (min_co + max_co) / 2
        if distance is None:
            radius = (max_co - min_co).length / 2
            if radius < 0.001:
                radius = 1.0
            fov_rad = math.radians(50)
            distance = radius / math.sin(fov_rad / 2) * 1.2

    # Safety: never place camera closer than 1mm to the target
    # (prevents black renders when zoom is too high or object is tiny)
    if distance is not None:
        distance = max(distance, 1.0)

    elev = math.radians(elevation_deg)
    azim = math.radians(azimuth_deg)

    x = distance * math.cos(elev) * math.sin(azim)
    y = -distance * math.cos(elev) * math.cos(azim)
    z = distance * math.sin(elev)

    cam_data = bpy.data.cameras.new("_agent_tmp_cam")
    cam_data.lens = 50
    cam_obj = bpy.data.objects.new("_agent_tmp_cam", cam_data)
    bpy.context.collection.objects.link(cam_obj)

    cam_obj.location = target + Vector((x, y, z))
    direction = target - cam_obj.location
    rot_quat = direction.to_track_quat('-Z', 'Y')
    cam_obj.rotation_euler = rot_quat.to_euler()

    return cam_obj


def render_to_base64(width=800, height=800, camera=None, transparent=False):
    """Render the scene through the given camera and return PNG as base64 string."""
    scene = bpy.context.scene

    # Save original settings
    orig_cam = scene.camera
    orig_x = scene.render.resolution_x
    orig_y = scene.render.resolution_y
    orig_pct = scene.render.resolution_percentage
    orig_format = scene.render.image_settings.file_format
    orig_film = scene.render.film_transparent

    try:
        if camera:
            scene.camera = camera
        scene.render.resolution_x = width
        scene.render.resolution_y = height
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = 'PNG'
        scene.render.film_transparent = transparent

        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        tmp.close()
        scene.render.filepath = tmp.name

        bpy.ops.render.render(write_still=True)

        with open(tmp.name, 'rb') as f:
            data = base64.b64encode(f.read()).decode('ascii')
        return data
    finally:
        scene.camera = orig_cam
        scene.render.resolution_x = orig_x
        scene.render.resolution_y = orig_y
        scene.render.resolution_percentage = orig_pct
        scene.render.image_settings.file_format = orig_format
        scene.render.film_transparent = orig_film
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def render_angle(label, elevation, azimuth, width=400, height=400):
    """Render from a specific angle. Returns (base64_png, label)."""
    cam = setup_render_camera(elevation, azimuth)
    try:
        b64 = render_to_base64(width, height, camera=cam)
    finally:
        cam_data = cam.data
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
    return b64, label


def set_vertex_colors(obj, face_colors, layer_name="PrintCheck"):
    """Set per-face vertex colors on a mesh.

    face_colors: dict of face_index -> (r, g, b, a)
    """
    mesh = obj.data
    if layer_name not in mesh.color_attributes:
        mesh.color_attributes.new(name=layer_name, type='FLOAT_COLOR', domain='CORNER')

    color_layer = mesh.color_attributes[layer_name]
    # Default to green
    for poly in mesh.polygons:
        color = face_colors.get(poly.index, (0.0, 0.8, 0.0, 1.0))
        for loop_idx in poly.loop_indices:
            color_layer.data[loop_idx].color = color


def checkpoint_path():
    """Where auto-checkpoints are written: next to the saved file, else temp."""
    if bpy.data.filepath:
        return bpy.data.filepath.replace('.blend', '_checkpoint.blend')
    return os.path.join(tempfile.gettempdir(), 'printable_blender_checkpoint.blend')


def auto_save_checkpoint():
    """Save a .blend checkpoint if the file has been saved before, or to temp."""
    path = checkpoint_path()
    # Stamp per-view-layer hide state into a custom property so restore can
    # re-apply it (#22). hide_set() state lives in the view layer and is not
    # carried when the checkpoint's objects are appended back.
    for obj in bpy.context.scene.objects:
        try:
            obj['_printable_hidden'] = obj.hide_get()
        except RuntimeError:
            pass  # object not in the active view layer
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    return path
