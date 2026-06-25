import argparse
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
BEFORE_FBX = ROOT / "output" / "fbx" / "junctions" / "J0109.fbx"
AFTER_FBX = ROOT / "output" / "fbx" / "modules" / "cim4" / "city_roads.fbx"
BEFORE_OUT = ROOT / "output" / "junction_case" / "j0109_before.png"
AFTER_OUT = ROOT / "output" / "junction_case" / "j0109_after.png"


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.cameras,
        bpy.data.lights,
    ):
        for block in list(datablocks):
            if block.users == 0:
                datablocks.remove(block)


def mesh_bounds(objects):
    points = [
        obj.matrix_world @ Vector(corner)
        for obj in objects
        if obj.type == "MESH" and not obj.hide_render
        for corner in obj.bound_box
    ]
    if not points:
        raise RuntimeError("No visible mesh bounds found")
    min_x = min(point.x for point in points)
    max_x = max(point.x for point in points)
    min_z = min(point.z for point in points)
    max_z = max(point.z for point in points)
    return min_x, max_x, min_z, max_z


def setup_camera(target_x: float, target_z: float, scale: float):
    camera_data = bpy.data.cameras.new("J0109_Debug_Camera")
    camera = bpy.data.objects.new("J0109_Debug_Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = Vector((target_x, -max(scale * 2.5, 250.0), target_z))
    target = Vector((target_x, 0.0, target_z))
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = max(scale, 60.0)
    bpy.context.scene.camera = camera
    return camera


def setup_render(out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.render.resolution_x = 1400
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.world.color = (0.055, 0.055, 0.055)
    scene.render.filepath = str(out_path)


def render_before():
    clear_scene()
    bpy.ops.import_scene.fbx(filepath=str(BEFORE_FBX))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    min_x, max_x, min_z, max_z = mesh_bounds(meshes)
    center_x = (min_x + max_x) * 0.5
    center_z = (min_z + max_z) * 0.5
    scale = max(max_x - min_x, (max_z - min_z) * 1.55) * 1.15
    setup_camera(center_x, center_z, scale)
    setup_render(BEFORE_OUT)
    bpy.ops.render.render(write_still=True)
    print(f"J0109_BEFORE={BEFORE_OUT}")


def render_after():
    clear_scene()
    bpy.ops.import_scene.fbx(filepath=str(AFTER_FBX))
    # J0109 center from the current CIM4 junction semantic output. FBX uses
    # Blender's X/Z plane for the source GIS X/Y plane.
    target_x = -1121.325
    target_z = -867.181
    setup_camera(target_x, target_z, 180.0)
    setup_render(AFTER_OUT)
    bpy.ops.render.render(write_still=True)
    print(f"J0109_AFTER={AFTER_OUT}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--view", choices=("before", "after", "both"), default="both")
    script_args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    args = parser.parse_args(script_args)
    if args.view in {"before", "both"}:
        render_before()
    if args.view in {"after", "both"}:
        render_after()
