from __future__ import annotations

from pathlib import Path
import math
import sys

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "tmp" / "pptx_replacements"


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def mesh_bounds() -> tuple[Vector, Vector]:
    mins = Vector((math.inf, math.inf, math.inf))
    maxs = Vector((-math.inf, -math.inf, -math.inf))
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or obj.hide_render:
            continue
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            mins.x = min(mins.x, point.x)
            mins.y = min(mins.y, point.y)
            mins.z = min(mins.z, point.z)
            maxs.x = max(maxs.x, point.x)
            maxs.y = max(maxs.y, point.y)
            maxs.z = max(maxs.z, point.z)
    if not math.isfinite(mins.x):
        raise RuntimeError("No visible mesh objects found.")
    return mins, maxs


def setup_scene(width: int, height: int, world_color: tuple[float, float, float]) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.film_transparent = False
    scene.world.color = world_color


def ensure_camera() -> bpy.types.Object:
    camera = bpy.context.scene.camera
    if camera is not None:
        return camera
    cam_data = bpy.data.cameras.new("PPT_Render_Camera")
    camera = bpy.data.objects.new("PPT_Render_Camera", cam_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    return camera


def render_top(name: str, target: Vector, ortho_scale: float, width: int = 2000, height: int = 1125) -> None:
    setup_scene(width, height, (0.72, 0.73, 0.72))
    camera = ensure_camera()
    camera.location = target + Vector((0.0, -900.0, 0.0))
    direction = target - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = ortho_scale
    camera.data.clip_start = 0.1
    camera.data.clip_end = 5000.0
    out_path = OUT_DIR / f"{name}.png"
    bpy.context.scene.render.filepath = str(out_path)
    bpy.ops.render.render(write_still=True)
    print(f"rendered={out_path}")


def render_perspective(name: str, target: Vector, distance: float, width: int = 2000, height: int = 1125) -> None:
    setup_scene(width, height, (0.78, 0.78, 0.76))
    camera = ensure_camera()
    camera.location = target + Vector((distance * 0.75, -distance, distance * 0.45))
    direction = target - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = distance * 0.55
    camera.data.clip_start = 0.1
    camera.data.clip_end = distance * 5.0
    out_path = OUT_DIR / f"{name}.png"
    bpy.context.scene.render.filepath = str(out_path)
    bpy.ops.render.render(write_still=True)
    print(f"rendered={out_path}")


def import_fbx(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    clear_scene()
    bpy.ops.import_scene.fbx(filepath=str(path))


def hide_by_prefix(prefixes: tuple[str, ...]) -> None:
    for obj in bpy.context.scene.objects:
        if obj.name.startswith(prefixes):
            obj.hide_render = True


def render_roads() -> None:
    import_fbx(ROOT / "output" / "fbx" / "modules" / "cim4" / "city_roads.fbx")
    hide_by_prefix(("Tree_", "Street_Light", "Junction_Label"))
    mins, maxs = mesh_bounds()
    center = (mins + maxs) * 0.5
    width = maxs.x - mins.x
    depth = maxs.z - mins.z
    overview_scale = max(width, depth) * 0.72
    detail_scale = max(240.0, min(520.0, overview_scale * 0.30))

    render_top("road_overview", Vector((center.x, -0.01, center.z)), overview_scale)
    render_top("road_core", Vector((center.x, -0.01, center.z)), detail_scale)
    render_top("road_north", Vector((center.x, -0.01, center.z + depth * 0.20)), detail_scale * 0.82)
    render_top("road_south", Vector((center.x, -0.01, center.z - depth * 0.20)), detail_scale * 0.82)
    render_top("road_ramp_detail", Vector((667.197, -0.01, -890.0)), 180.0)
    render_perspective("road_perspective", Vector((center.x, -0.01, center.z)), max(width, depth) * 0.62)


def render_junction(junction_id: str, name: str) -> None:
    import_fbx(ROOT / "output" / "fbx" / "junctions" / f"{junction_id}.fbx")
    mins, maxs = mesh_bounds()
    center = (mins + maxs) * 0.5
    span = max(maxs.x - mins.x, maxs.z - mins.z, 80.0)
    render_perspective(name, Vector((center.x, center.y, center.z)), span * 2.6)


def render_subway() -> None:
    import_fbx(ROOT / "output" / "fbx" / "modules" / "cim4" / "subway_tunnels.fbx")
    mins, maxs = mesh_bounds()
    center = (mins + maxs) * 0.5
    span = max(maxs.x - mins.x, maxs.z - mins.z, 120.0)
    render_top("subway_overview", Vector((center.x, center.y, center.z)), span * 0.68)
    render_perspective("subway_perspective", Vector((center.x, center.y, center.z)), span * 0.78)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    render_roads()
    render_junction("J0006", "junction_j0006")
    render_junction("J0038", "junction_j0038")
    render_subway()


if __name__ == "__main__":
    main()
