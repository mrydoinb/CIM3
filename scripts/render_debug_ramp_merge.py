from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
FBX_PATH = ROOT / "output" / "fbx" / "modules" / "cim_city_roads.fbx"
OUT_PATH = ROOT / "output" / "debug_ramp_merge_nonmotor_latest.png"


def clear_scene() -> None:
    for obj in list(bpy.context.scene.objects):
        if obj.type in {"MESH", "CURVE", "FONT", "EMPTY", "LIGHT", "CAMERA"}:
            bpy.data.objects.remove(obj, do_unlink=True)


def render_debug() -> None:
    clear_scene()
    bpy.ops.import_scene.fbx(filepath=str(FBX_PATH))

    target = Vector((667.197, -7.071, -890.0))
    cam_data = bpy.data.cameras.new("Codex_Debug_Camera")
    cam = bpy.data.objects.new("Codex_Debug_Camera", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = Vector((target.x, -450.0, target.z))
    direction = target - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 180.0
    bpy.context.scene.camera = cam

    sun_data = bpy.data.lights.new("Codex_Debug_Sun", type="SUN")
    sun = bpy.data.objects.new("Codex_Debug_Sun", sun_data)
    bpy.context.collection.objects.link(sun)
    sun.location = Vector((target.x, -200.0, target.z + 200.0))
    sun.rotation_euler = (0.8, 0.0, 0.2)
    sun.data.energy = 2.0

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.render.resolution_x = 1400
    scene.render.resolution_y = 1050
    scene.render.film_transparent = False
    scene.world.color = (0.18, 0.18, 0.18)

    bpy.ops.render.render(write_still=False)
    bpy.data.images["Render Result"].save_render(filepath=str(OUT_PATH))
    print(f"DEBUG_RENDER={OUT_PATH}")


if __name__ == "__main__":
    render_debug()
