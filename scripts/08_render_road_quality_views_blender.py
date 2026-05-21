#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Render top-down road quality inspection views from the exported roads FBX."""

from __future__ import annotations

from pathlib import Path
import math

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
FBX_PATH = ROOT / "output" / "fbx" / "modules" / "cim_city_roads.fbx"
OUT_DIR = ROOT / "output" / "render"

HIDE_PREFIXES = (
    "Tree_",
    "Street_Light",
    "Junction_Label",
    "Guardrail_",
    "Bridge_",
)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def import_roads_fbx() -> None:
    if not FBX_PATH.exists():
        raise FileNotFoundError(f"Missing FBX input: {FBX_PATH}")
    bpy.ops.import_scene.fbx(filepath=str(FBX_PATH))
    for obj in bpy.context.scene.objects:
        if obj.name.startswith(HIDE_PREFIXES):
            obj.hide_render = True


def visible_mesh_bounds() -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    zs: list[float] = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or obj.hide_render:
            continue
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            xs.append(float(world.x))
            zs.append(float(world.z))
    if not xs or not zs:
        return None
    return min(xs), min(zs), max(xs), max(zs)


def setup_render() -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 1100

    cam_data = bpy.data.cameras.new("RoadQualityCamera")
    cam = bpy.data.objects.new("RoadQualityCamera", cam_data)
    bpy.context.collection.objects.link(cam)
    scene.camera = cam
    return cam


def render_view(camera: bpy.types.Object, name: str, target: Vector, ortho_scale: float) -> None:
    camera.location = target + Vector((0.0, -900.0, 0.0))
    direction = target - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = ortho_scale
    bpy.context.scene.render.filepath = str(OUT_DIR / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    print(f"Rendered {bpy.context.scene.render.filepath}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    clear_scene()
    import_roads_fbx()
    bounds = visible_mesh_bounds()
    if bounds is None:
        raise RuntimeError("No visible mesh bounds found.")

    min_x, min_z, max_x, max_z = bounds
    center_x = (min_x + max_x) * 0.5
    center_z = (min_z + max_z) * 0.5
    width = max_x - min_x
    height = max_z - min_z
    overview_scale = max(width, height) * 0.72
    detail_scale = max(240.0, min(430.0, overview_scale * 0.28))

    camera = setup_render()
    render_view(camera, "road_quality_overview", Vector((center_x, -0.01, center_z)), overview_scale)
    render_view(camera, "road_quality_core", Vector((center_x, -0.01, center_z)), detail_scale)
    render_view(camera, "road_quality_north", Vector((center_x, -0.01, center_z + height * 0.18)), detail_scale * 0.72)
    render_view(camera, "road_quality_south", Vector((center_x, -0.01, center_z - height * 0.18)), detail_scale * 0.72)


if __name__ == "__main__":
    main()
